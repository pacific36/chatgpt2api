"use client";

import localforage from "localforage";

export type ChatRole = "user" | "assistant";

export type ChatMessageStatus = "streaming" | "done" | "error";

export type ChatAttachmentKind = "image" | "file";

export type ChatAttachment = {
  name: string;
  type: string;
  size: number;
  dataUrl: string;
  kind: ChatAttachmentKind;
};

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  status: ChatMessageStatus;
  model?: string;
  error?: string;
  attachments?: ChatAttachment[];
};

export type ChatConversation = {
  id: string;
  title: string;
  model: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
};

const chatConversationStorage = localforage.createInstance({
  name: "chatgpt2api",
  storeName: "chat_conversations",
});

const CHAT_CONVERSATIONS_KEY = "items";
let chatConversationWriteQueue: Promise<void> = Promise.resolve();

function normalizeAttachments(value: unknown): ChatAttachment[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const attachments = value.filter((item): item is ChatAttachment => {
    if (!item || typeof item !== "object") {
      return false;
    }
    const candidate = item as ChatAttachment;
    return typeof candidate.dataUrl === "string" && candidate.dataUrl.startsWith("data:");
  });
  if (attachments.length === 0) {
    return undefined;
  }
  return attachments.map((item) => ({
    name: String(item.name || "file"),
    type: String(item.type || "application/octet-stream"),
    size: Math.max(0, Number(item.size) || 0),
    dataUrl: item.dataUrl,
    kind: item.kind === "image" ? "image" : "file",
  }));
}

function normalizeMessage(message: ChatMessage & Record<string, unknown>): ChatMessage {
  return {
    id: String(message.id || `${Date.now()}`),
    role: message.role === "assistant" ? "assistant" : "user",
    content: String(message.content || ""),
    createdAt: String(message.createdAt || new Date().toISOString()),
    // A reloaded page can never resume an in-flight stream.
    status: message.status === "error" ? "error" : "done",
    model: typeof message.model === "string" && message.model ? message.model : undefined,
    error: typeof message.error === "string" && message.error ? message.error : undefined,
    attachments: normalizeAttachments(message.attachments),
  };
}

function normalizeConversation(conversation: ChatConversation & Record<string, unknown>): ChatConversation {
  const messages = Array.isArray(conversation.messages)
    ? conversation.messages.map((message) => normalizeMessage(message as ChatMessage & Record<string, unknown>))
    : [];
  return {
    id: String(conversation.id || `${Date.now()}`),
    title: String(conversation.title || ""),
    model: typeof conversation.model === "string" && conversation.model ? conversation.model : "auto",
    createdAt: String(conversation.createdAt || new Date().toISOString()),
    updatedAt: String(conversation.updatedAt || new Date().toISOString()),
    messages,
  };
}

function sortChatConversations(conversations: ChatConversation[]): ChatConversation[] {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function queueChatConversationWrite<T>(operation: () => Promise<T>): Promise<T> {
  const result = chatConversationWriteQueue.then(operation);
  chatConversationWriteQueue = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

async function readStoredChatConversations(): Promise<ChatConversation[]> {
  const items =
    (await chatConversationStorage.getItem<Array<ChatConversation & Record<string, unknown>>>(
      CHAT_CONVERSATIONS_KEY,
    )) || [];
  return items.map(normalizeConversation);
}

export async function listChatConversations(): Promise<ChatConversation[]> {
  return sortChatConversations(await readStoredChatConversations());
}

export async function saveChatConversation(conversation: ChatConversation): Promise<void> {
  await queueChatConversationWrite(async () => {
    const items = await readStoredChatConversations();
    const nextConversation = normalizeConversation(conversation);
    const nextItems = sortChatConversations([
      nextConversation,
      ...items.filter((item) => item.id !== nextConversation.id),
    ]);
    await chatConversationStorage.setItem(CHAT_CONVERSATIONS_KEY, nextItems);
  });
}

export async function renameChatConversation(id: string, title: string): Promise<void> {
  await queueChatConversationWrite(async () => {
    const items = await readStoredChatConversations();
    const target = items.find((item) => item.id === id);
    if (!target) return;
    const updated = { ...target, title, updatedAt: new Date().toISOString() };
    await chatConversationStorage.setItem(
      CHAT_CONVERSATIONS_KEY,
      sortChatConversations([updated, ...items.filter((item) => item.id !== id)]),
    );
  });
}

export async function deleteChatConversation(id: string): Promise<void> {
  await queueChatConversationWrite(async () => {
    const items = await readStoredChatConversations();
    await chatConversationStorage.setItem(
      CHAT_CONVERSATIONS_KEY,
      items.filter((item) => item.id !== id),
    );
  });
}

export async function clearChatConversations(): Promise<void> {
  await queueChatConversationWrite(async () => {
    await chatConversationStorage.removeItem(CHAT_CONVERSATIONS_KEY);
  });
}
