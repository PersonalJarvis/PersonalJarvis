/** Duration of each microphone packet sent from the audio render thread. */
export const CAPTURE_PACKET_SECONDS = 0.02;

/**
 * Maximum playback backlog retained inside the AudioWorklet.
 *
 * Ten seconds absorbs substantial scheduler/network jitter while keeping the
 * queue below 1 MiB at 48 kHz (PCM16). If a provider outruns playback beyond
 * this bound, the queue drops its oldest samples so latency cannot grow
 * without limit.
 */
export const MAX_PLAYBACK_BUFFER_SECONDS = 10;

function validatedSampleCount(sampleRate: number, seconds: number): number {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    throw new RangeError("sampleRate must be a positive finite number");
  }
  return Math.max(1, Math.round(sampleRate * seconds));
}

export function capturePacketSampleCount(sampleRate: number): number {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) {
    throw new RangeError("sampleRate must be a positive finite number");
  }
  // Round upward so even an unusual non-divisible context rate never crosses
  // the 50 messages/s ceiling.
  return Math.max(1, Math.ceil(sampleRate * CAPTURE_PACKET_SECONDS));
}

export function playbackBufferSampleCount(sampleRate: number): number {
  return validatedSampleCount(sampleRate, MAX_PLAYBACK_BUFFER_SECONDS);
}

function floatToPcm16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample));
  return clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
}

/** Coalesces arbitrary render quanta into fixed-duration PCM16 packets. */
export class Pcm16Packetizer {
  readonly packetSamples: number;
  private packet: Int16Array;
  private used = 0;

  constructor(sampleRate: number) {
    this.packetSamples = capturePacketSampleCount(sampleRate);
    this.packet = new Int16Array(this.packetSamples);
  }

  push(samples: Float32Array, emit: (packet: ArrayBuffer) => void): void {
    let sourceOffset = 0;
    while (sourceOffset < samples.length) {
      const copyCount = Math.min(
        this.packetSamples - this.used,
        samples.length - sourceOffset,
      );
      for (let i = 0; i < copyCount; i++) {
        this.packet[this.used + i] = floatToPcm16(samples[sourceOffset + i]);
      }
      this.used += copyCount;
      sourceOffset += copyCount;

      if (this.used === this.packetSamples) {
        const completed = this.packet;
        this.packet = new Int16Array(this.packetSamples);
        this.used = 0;
        // Every packet is allocated above with `new Int16Array(length)`, so
        // its backing store is always a transferable ArrayBuffer.
        emit(completed.buffer as ArrayBuffer);
      }
    }
  }
}

/**
 * Fixed-capacity PCM16 ring used by the playback render thread.
 *
 * On overrun, enqueue() drops the oldest queued samples and returns the number
 * dropped. Retained samples always preserve their original order. This favors
 * current speech over an ever-growing stale backlog.
 */
export class BoundedPcm16Queue {
  private readonly samples: Int16Array;
  private readIndex = 0;
  private writeIndex = 0;
  private queued = 0;

  constructor(readonly capacity: number) {
    if (!Number.isInteger(capacity) || capacity <= 0) {
      throw new RangeError("capacity must be a positive integer");
    }
    this.samples = new Int16Array(capacity);
  }

  get length(): number {
    return this.queued;
  }

  clear(): void {
    this.readIndex = 0;
    this.writeIndex = 0;
    this.queued = 0;
  }

  enqueue(input: Int16Array): number {
    if (input.length === 0) return 0;

    if (input.length >= this.capacity) {
      const dropped = this.queued + input.length - this.capacity;
      this.samples.set(input.subarray(input.length - this.capacity));
      this.readIndex = 0;
      this.writeIndex = 0;
      this.queued = this.capacity;
      return dropped;
    }

    const dropped = Math.max(0, this.queued + input.length - this.capacity);
    if (dropped > 0) {
      this.readIndex = (this.readIndex + dropped) % this.capacity;
      this.queued -= dropped;
    }

    const firstCopy = Math.min(input.length, this.capacity - this.writeIndex);
    this.samples.set(input.subarray(0, firstCopy), this.writeIndex);
    if (firstCopy < input.length) {
      this.samples.set(input.subarray(firstCopy), 0);
    }
    this.writeIndex = (this.writeIndex + input.length) % this.capacity;
    this.queued += input.length;
    return dropped;
  }

  dequeueInto(output: Float32Array): number {
    output.fill(0);
    const readCount = Math.min(output.length, this.queued);
    const firstRead = Math.min(readCount, this.capacity - this.readIndex);

    for (let i = 0; i < firstRead; i++) {
      output[i] = this.samples[this.readIndex + i] / 0x8000;
    }
    const secondRead = readCount - firstRead;
    for (let i = 0; i < secondRead; i++) {
      output[firstRead + i] = this.samples[i] / 0x8000;
    }

    this.readIndex = (this.readIndex + readCount) % this.capacity;
    this.queued -= readCount;
    return readCount;
  }
}
