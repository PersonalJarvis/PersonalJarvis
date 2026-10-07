import { Tokenizer } from "@huggingface/tokenizers";
import { env, InferenceSession, Tensor } from "onnxruntime-web/wasm";

export interface AgentEncoder {
  embed: (text: string) => Promise<number[]>;
  dispose: () => Promise<void>;
}

/** One unpadded sentence, mean pooled across its token embeddings. */
export function meanPoolTokens(data: Float32Array, tokens: number, dimensions: number): number[] {
  if (!tokens || !dimensions || data.length !== tokens * dimensions) throw new Error("Invalid search embedding shape");
  const vector = Array<number>(dimensions).fill(0);
  for (let token = 0; token < tokens; token += 1) {
    for (let dim = 0; dim < dimensions; dim += 1) vector[dim] += data[token * dimensions + dim] / tokens;
  }
  return vector;
}

/** The pinned MiniLM encoder only: no chat, generation or other model registry. */
export async function createAgentEncoder(loadAsset: (file: string) => Promise<Uint8Array>): Promise<AgentEncoder> {
  env.wasm.numThreads = 1;
  env.wasm.proxy = false;
  const [tokenizerBytes, configBytes, modelBytes] = await Promise.all([
    loadAsset("tokenizer.json"),
    loadAsset("tokenizer_config.json"),
    loadAsset("onnx/model_quantized.onnx"),
  ]);
  const decode = (bytes: Uint8Array) => JSON.parse(new TextDecoder().decode(bytes));
  const tokenizer = new Tokenizer(decode(tokenizerBytes), decode(configBytes));
  const session = await InferenceSession.create(modelBytes, { executionProviders: ["wasm"] });
  return {
    async embed(text) {
      const encoded = tokenizer.encode(text, { add_special_tokens: true });
      // Match MiniLM's 128-token sentence limit while retaining its final SEP.
      const ids = encoded.ids.length > 128 ? [...encoded.ids.slice(0, 127), encoded.ids.at(-1)!] : encoded.ids;
      const dims = [1, ids.length];
      const feeds: Record<string, Tensor> = {
        input_ids: new Tensor("int64", BigInt64Array.from(ids, BigInt), dims),
        attention_mask: new Tensor("int64", new BigInt64Array(ids.length).fill(1n), dims),
      };
      if (session.inputNames.includes("token_type_ids")) feeds.token_type_ids = new Tensor("int64", new BigInt64Array(ids.length), dims);
      let output: Record<string, Tensor> = {};
      try {
        output = await session.run(feeds);
        const hidden = output.last_hidden_state;
        if (!hidden || hidden.type !== "float32" || hidden.dims.length !== 3 || hidden.dims[0] !== 1) {
          throw new Error("Invalid search encoder output");
        }
        return meanPoolTokens(hidden.data as Float32Array, hidden.dims[1], hidden.dims[2]);
      } finally {
        for (const tensor of Object.values(output)) tensor.dispose();
        for (const tensor of Object.values(feeds)) tensor.dispose();
      }
    },
    dispose: () => session.release(),
  };
}
