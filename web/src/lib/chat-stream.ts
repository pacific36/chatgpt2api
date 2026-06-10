"use client";

import webConfig from "@/constants/common-env";
import { clearStoredAuthSession, getStoredAuthKey } from "@/store/auth";

export type ChatContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } }
  | { type: "file"; file: { filename: string; file_data: string } };

export type ChatCompletionMessage = {
  role: "system" | "user" | "assistant";
  content: string | ChatContentPart[];
};

type StreamChatOptions = {
  model: string;
  messages: ChatCompletionMessage[];
  signal?: AbortSignal;
  onDelta: (text: string) => void;
};

type ChatChunk = {
  choices?: Array<{ delta?: { content?: string }; finish_reason?: string | null }>;
  error?: { message?: string } | string;
};

function chunkErrorMessage(error: ChatChunk["error"]): string {
  if (typeof error === "string") {
    return error;
  }
  return error?.message || "对话请求失败";
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { error?: { message?: string } | string; detail?: unknown };
    if (payload.error) {
      return chunkErrorMessage(payload.error);
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    // fall through
  }
  return `对话请求失败 (${response.status})`;
}

/**
 * Stream a /v1/chat/completions response. Resolves with the full text once the
 * stream ends; partial text already delivered through onDelta is kept by the
 * caller when the request is aborted.
 */
export async function streamChatCompletion({ model, messages, signal, onDelta }: StreamChatOptions): Promise<string> {
  const authKey = await getStoredAuthKey();
  const baseUrl = webConfig.apiUrl.replace(/\/$/, "");
  const response = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authKey ? { Authorization: `Bearer ${authKey}` } : {}),
    },
    body: JSON.stringify({ model, messages, stream: true }),
    signal,
  });

  if (response.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    await clearStoredAuthSession();
    window.location.replace("/login");
    return new Promise(() => {});
  }
  if (!response.ok || !response.body) {
    throw new Error(await readErrorMessage(response));
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let fullText = "";

  const consumeLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) {
      return false;
    }
    const payload = trimmed.slice(5).trim();
    if (!payload || payload === "[DONE]") {
      return payload === "[DONE]";
    }
    let chunk: ChatChunk;
    try {
      chunk = JSON.parse(payload) as ChatChunk;
    } catch {
      return false;
    }
    if (chunk.error) {
      throw new Error(chunkErrorMessage(chunk.error));
    }
    const delta = chunk.choices?.[0]?.delta?.content;
    if (delta) {
      fullText += delta;
      onDelta(delta);
    }
    return false;
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let newlineIndex = buffer.indexOf("\n");
      while (newlineIndex !== -1) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        if (consumeLine(line)) {
          return fullText;
        }
        newlineIndex = buffer.indexOf("\n");
      }
    }
    if (buffer) {
      consumeLine(buffer);
    }
  } finally {
    reader.releaseLock();
  }
  return fullText;
}
