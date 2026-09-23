// Jarvis-owned native audio worker. No network listener and no tool execution.
// The parent owns model acquisition, permissions, audio devices and recovery.
#include "arg.h"
#include "base64.hpp"
#include "log.h"
#include "runner.h"
#include <nlohmann/json.hpp>

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iterator>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

using json = nlohmann::json;
using Runner = liquid::audio::Runner;
constexpr size_t MAX_REQUEST_BYTES = 32 * 1024 * 1024;

struct Job {
    std::string id;
    std::vector<Runner::Message> messages;
    std::vector<mtmd_output_modality> modalities;
    int max_tokens = 32768;
    bool reset = true;
    bool emit_text = true;
    bool emit_audio = true;
};

static size_t complete_utf8_prefix(const std::string & text) {
    if (text.empty()) return 0;
    size_t lead = text.size() - 1;
    while (lead > 0 && (static_cast<unsigned char>(text[lead]) & 0xc0) == 0x80) --lead;
    const auto byte = static_cast<unsigned char>(text[lead]);
    const size_t expected = byte < 0x80 ? 1 : byte < 0xe0 ? 2 : byte < 0xf0 ? 3 : 4;
    return text.size() - lead < expected ? lead : text.size();
}

int main(int argc, char ** argv) {
    common_params params;
    if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_LIQUID_AUDIO, nullptr)) return 2;
    // Upstream's human-readable output includes transcripts and performance
    // lines on stdout. Pipes carry only our framed events; failures below have
    // structured codes, so suppress that auxiliary output before model loading.
    common_log_pause(common_log_main());
    Runner runner;
    if (runner.init(params) != 0) return 3;

    std::mutex output_mutex;
    auto emit = [&](json value) {
        std::lock_guard<std::mutex> guard(output_mutex);
        std::cout << value.dump(-1, ' ', false, json::error_handler_t::replace) << std::endl;
    };
    auto error = [&](const std::string & id, const std::string & code) {
        emit({{"kind", "error"}, {"id", id}, {"code", code}});
    };

    std::mutex state_mutex;
    std::condition_variable changed;
    std::optional<Job> queued;
    std::string active_id;
    bool busy = false;
    bool context_valid = false;
    bool stopping = false;
    std::atomic<bool> cancelled{false};

    std::thread inference([&]() {
        while (true) {
            Job job;
            {
                std::unique_lock<std::mutex> lock(state_mutex);
                changed.wait(lock, [&]() { return stopping || queued.has_value(); });
                if (stopping && !queued) return;
                job = std::move(*queued);
                queued.reset();
                // reset() clears the engine's stop flag. Serialize it with
                // cancellation so a late reset cannot undo an accepted cancel.
                if (!cancelled && job.reset) runner.reset();
            }
            bool failed = false;
            std::string pending_text;
            auto text = [&](const std::string & part) {
                if (cancelled || !job.emit_text) return;
                pending_text += part;
                const size_t count = complete_utf8_prefix(pending_text);
                if (count) {
                    emit({{"kind", "text"}, {"id", job.id}, {"text", pending_text.substr(0, count)}});
                    pending_text.erase(0, count);
                }
            };
            auto audio = [&](const std::vector<int16_t> & samples) {
                if (cancelled || !job.emit_audio || samples.empty()) return;
                const auto encoded = base64::encode(
                    reinterpret_cast<const char *>(samples.data()), samples.size() * sizeof(int16_t));
                emit({{"kind", "audio"}, {"id", job.id}, {"pcm", encoded},
                      {"sample_rate", runner.get_output_sample_rate()}});
            };
            try {
                if (!cancelled) {
                    failed = runner.generate(job.messages, job.max_tokens, text, audio, job.modalities) != 0;
                    failed = failed || !pending_text.empty();
                }
            } catch (const std::exception &) {
                // Raw model errors may contain a transcript. Report a stable
                // error code through the control protocol, never its body.
                failed = true;
            }
            {
                std::lock_guard<std::mutex> lock(state_mutex);
                context_valid = !failed && !cancelled;
                if (cancelled) emit({{"kind", "cancelled"}, {"id", job.id}});
                else if (failed) error(job.id, "inference_failed");
                else emit({{"kind", "done"}, {"id", job.id}});
                busy = false;
                active_id.clear();
            }
        }
    });

    emit({{"kind", "loaded"}, {"protocol", 1}, {"engine", "lfm2-audio"},
          {"revision", JARVIS_LFM_REVISION}, {"sample_rate", runner.get_output_sample_rate()},
          {"tools", false}, {"full_duplex", false}});

    std::string line;
    while (std::cin.good()) {
        line.clear();
        char ch;
        bool too_large = false;
        while (std::cin.get(ch) && ch != '\n') {
            if (line.size() >= MAX_REQUEST_BYTES) {
                too_large = true;
                std::cin.ignore(std::numeric_limits<std::streamsize>::max(), '\n');
                break;
            }
            line.push_back(ch);
        }
        if (too_large) { error("", "request_too_large"); continue; }
        if (line.empty()) continue;
        std::string id;
        try {
            const auto request = json::parse(line);
            id = request.value("id", "");
            if (id.empty() || id.size() > 128) throw std::invalid_argument("id");
            const auto command = request.at("command").get<std::string>();
            if (command == "shutdown") break;
            if (command == "cancel") {
                std::lock_guard<std::mutex> lock(state_mutex);
                if (!busy || id != active_id) error(id, "not_active");
                else { cancelled = true; runner.stop(); }
                continue;
            }
            if (command != "generate") throw std::invalid_argument("command");
            Job job;
            job.id = id;
            job.reset = request.value("reset_context", true);
            job.max_tokens = request.value("max_tokens", 32768);
            if (job.max_tokens < 1 || job.max_tokens > 32768) throw std::invalid_argument("tokens");
            const std::string mode = request.value("output_mode", "text_audio");
            if (mode != "text" && mode != "audio" && mode != "text_audio") throw std::invalid_argument("mode");
            job.emit_text = mode != "audio";
            job.emit_audio = mode != "text";
            if (job.emit_text) job.modalities.push_back(MTMD_OUTPUT_MODALITY_TEXT);
            if (job.emit_audio) job.modalities.push_back(MTMD_OUTPUT_MODALITY_AUDIO);
            if (job.reset) {
                job.messages.push_back({"system", request.value("instructions", Runner::interleaved_system_prompt), {}});
            }
            const auto text = request.value("text", "");
            if (!text.empty()) job.messages.push_back({"user", text, {}});
            if (request.contains("audio_wav")) {
                const auto encoded = request.at("audio_wav").get<std::string>();
                std::vector<uint8_t> decoded;
                base64::decode(encoded.begin(), encoded.end(), std::back_inserter(decoded));
                std::vector<std::byte> wav(decoded.size());
                std::memcpy(wav.data(), decoded.data(), decoded.size());
                job.messages.push_back({"user", mtmd_default_marker(), std::move(wav)});
            }
            if (job.messages.empty() || (job.reset && job.messages.size() == 1)) throw std::invalid_argument("input");
            {
                std::lock_guard<std::mutex> lock(state_mutex);
                if (busy) { error(id, "busy"); continue; }
                if (!job.reset && !context_valid) { error(id, "context_reset_required"); continue; }
                busy = true;
                context_valid = false;
                cancelled = false;
                active_id = id;
                queued = std::move(job);
            }
            changed.notify_one();
        } catch (const std::exception &) {
            error(id.size() <= 128 ? id : "", "invalid_request");
        }
    }
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        stopping = true;
        cancelled = true;
        runner.stop();
    }
    changed.notify_one();
    inference.join();
    return 0;
}
