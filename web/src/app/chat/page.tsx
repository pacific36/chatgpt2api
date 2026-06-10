"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, History, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ChatComposer } from "@/app/chat/components/chat-composer";
import { ChatMessages } from "@/app/chat/components/chat-messages";
import { ChatSidebar } from "@/app/chat/components/chat-sidebar";
import type { ChatContentPart } from "@/lib/chat-stream";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { fetchModels, type Model } from "@/lib/api";
import { streamChatCompletion, type ChatCompletionMessage } from "@/lib/chat-stream";
import { useAuthGuard } from "@/lib/use-auth-guard";
import {
  clearChatConversations,
  deleteChatConversation,
  listChatConversations,
  renameChatConversation,
  saveChatConversation,
  type ChatAttachment,
  type ChatConversation,
  type ChatMessage,
} from "@/store/chat-conversations";

const ACTIVE_CHAT_CONVERSATION_STORAGE_KEY = "chatgpt2api:chat_active_conversation_id";
const CHAT_MODEL_STORAGE_KEY = "chatgpt2api:chat_last_model";
const SCROLL_TO_LATEST_THRESHOLD = 160;
const STREAM_PERSIST_INTERVAL_MS = 1200;
const DEFAULT_TEXT_MODELS = ["auto"];

function createId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim().replace(/\s+/g, " ");
  return trimmed.length <= 16 ? trimmed : `${trimmed.slice(0, 16)}...`;
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function filterTextModels(items: Model[]): string[] {
  return items
    .map((item) => String(item.id || "").trim())
    .filter((id, index, list) => id && !id.toLowerCase().includes("image") && list.indexOf(id) === index);
}

const MAX_ATTACHMENT_COUNT = 10;
const MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024;

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error(`读取文件 ${file.name} 失败`));
    reader.readAsDataURL(file);
  });
}

function messageContent(message: ChatMessage): string | ChatContentPart[] {
  if (message.role === "assistant" || !message.attachments?.length) {
    return message.content;
  }
  const parts: ChatContentPart[] = [];
  if (message.content.trim()) {
    parts.push({ type: "text", text: message.content });
  }
  for (const attachment of message.attachments) {
    if (attachment.kind === "image") {
      parts.push({ type: "image_url", image_url: { url: attachment.dataUrl } });
    } else {
      parts.push({ type: "file", file: { filename: attachment.name, file_data: attachment.dataUrl } });
    }
  }
  return parts;
}

function buildCompletionMessages(messages: ChatMessage[]): ChatCompletionMessage[] {
  return messages
    .filter((message) => message.status !== "error" && (message.content.trim() || message.attachments?.length))
    .map((message) => ({ role: message.role, content: messageContent(message) }));
}

function sortChatConversations(conversations: ChatConversation[]) {
  return [...conversations].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function getDistanceFromBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight;
}

function ChatPageContent() {
  const conversationsRef = useRef<ChatConversation[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);
  const lastPersistAtRef = useRef(0);
  const viewportRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);
  const scrollRafRef = useRef<number | null>(null);
  const lastConversationIdRef = useRef<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("auto");
  const [models, setModels] = useState<string[]>(DEFAULT_TEXT_MODELS);
  const [streamingConversationId, setStreamingConversationId] = useState<string | null>(null);
  const [showScrollToLatest, setShowScrollToLatest] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: "one"; id: string } | { type: "all" } | null>(null);

  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );
  const isStreaming = streamingConversationId !== null;

  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
    };
  }, []);

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    const element = viewportRef.current;
    if (!element) {
      return;
    }
    shouldStickToBottomRef.current = true;
    setShowScrollToLatest(false);
    element.scrollTo({ top: element.scrollHeight, behavior });
  }, []);

  const handleViewportScroll = useCallback(() => {
    if (scrollRafRef.current !== null) {
      return;
    }
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = null;
      const element = viewportRef.current;
      if (!element) {
        return;
      }
      const isAway = getDistanceFromBottom(element) > SCROLL_TO_LATEST_THRESHOLD;
      shouldStickToBottomRef.current = !isAway;
      setShowScrollToLatest(isAway);
    });
  }, []);

  useEffect(() => {
    return () => {
      if (scrollRafRef.current !== null) {
        window.cancelAnimationFrame(scrollRafRef.current);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      try {
        const items = await listChatConversations();
        if (cancelled) {
          return;
        }
        conversationsRef.current = items;
        setConversations(items);
        const storedId =
          typeof window !== "undefined" ? window.localStorage.getItem(ACTIVE_CHAT_CONVERSATION_STORAGE_KEY) : null;
        setSelectedConversationId(
          storedId && items.some((item) => item.id === storedId) ? storedId : items[0]?.id ?? null,
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取会话记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    };

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadTextModels = async () => {
      try {
        const data = await fetchModels();
        const available = filterTextModels(Array.isArray(data.data) ? data.data : []);
        if (cancelled || available.length === 0) {
          return;
        }
        setModels(available);
        const storedModel = typeof window !== "undefined" ? window.localStorage.getItem(CHAT_MODEL_STORAGE_KEY) : null;
        setModel((current) => {
          if (available.includes(current)) {
            return current;
          }
          if (storedModel && available.includes(storedModel)) {
            return storedModel;
          }
          return available.includes("auto") ? "auto" : available[0];
        });
      } catch {
        if (!cancelled) {
          setModels(DEFAULT_TEXT_MODELS);
        }
      }
    };

    void loadTextModels();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(CHAT_MODEL_STORAGE_KEY, model);
  }, [model]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (selectedConversationId) {
      window.localStorage.setItem(ACTIVE_CHAT_CONVERSATION_STORAGE_KEY, selectedConversationId);
    } else {
      window.localStorage.removeItem(ACTIVE_CHAT_CONVERSATION_STORAGE_KEY);
    }
  }, [selectedConversationId]);

  useEffect(() => {
    if (!selectedConversation) {
      lastConversationIdRef.current = null;
      shouldStickToBottomRef.current = true;
      setShowScrollToLatest(false);
      return;
    }
    const element = viewportRef.current;
    if (!element) {
      return;
    }
    const didSwitch = lastConversationIdRef.current !== selectedConversation.id;
    lastConversationIdRef.current = selectedConversation.id;
    if (didSwitch) {
      requestAnimationFrame(() => scrollToLatest("auto"));
      return;
    }
    if (shouldStickToBottomRef.current || getDistanceFromBottom(element) <= SCROLL_TO_LATEST_THRESHOLD) {
      requestAnimationFrame(() => scrollToLatest("auto"));
      return;
    }
    setShowScrollToLatest(true);
  }, [selectedConversation, scrollToLatest]);

  const applyConversation = useCallback((conversation: ChatConversation) => {
    const nextConversations = sortChatConversations([
      conversation,
      ...conversationsRef.current.filter((item) => item.id !== conversation.id),
    ]);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
  }, []);

  const updateAssistantMessage = useCallback(
    (conversationId: string, messageId: string, updater: (message: ChatMessage) => ChatMessage) => {
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      if (!conversation) {
        return null;
      }
      const nextConversation: ChatConversation = {
        ...conversation,
        updatedAt: new Date().toISOString(),
        messages: conversation.messages.map((message) => (message.id === messageId ? updater(message) : message)),
      };
      applyConversation(nextConversation);
      return nextConversation;
    },
    [applyConversation],
  );

  const runChatStream = useCallback(
    async (conversation: ChatConversation, assistantMessageId: string, history: ChatCompletionMessage[]) => {
      const controller = new AbortController();
      abortControllerRef.current = controller;
      setStreamingConversationId(conversation.id);
      lastPersistAtRef.current = Date.now();

      const finalize = async (updater: (message: ChatMessage) => ChatMessage) => {
        const nextConversation = updateAssistantMessage(conversation.id, assistantMessageId, updater);
        if (nextConversation) {
          await saveChatConversation(nextConversation);
        }
      };

      try {
        await streamChatCompletion({
          model: conversation.model,
          messages: history,
          signal: controller.signal,
          onDelta: (delta) => {
            const nextConversation = updateAssistantMessage(conversation.id, assistantMessageId, (message) => ({
              ...message,
              content: message.content + delta,
            }));
            if (nextConversation && Date.now() - lastPersistAtRef.current >= STREAM_PERSIST_INTERVAL_MS) {
              lastPersistAtRef.current = Date.now();
              void saveChatConversation(nextConversation);
            }
          },
        });
        await finalize((message) => ({ ...message, status: "done" }));
      } catch (error) {
        if (controller.signal.aborted) {
          await finalize((message) => ({
            ...message,
            status: "done",
            content: message.content || "（已停止生成）",
          }));
        } else {
          const message = error instanceof Error ? error.message : "对话请求失败";
          await finalize((current) => ({ ...current, status: "error", error: message }));
          toast.error(message);
        }
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
        setStreamingConversationId((current) => (current === conversation.id ? null : current));
      }
    },
    [updateAssistantMessage],
  );

  const handleFilesChange = useCallback(
    async (files: File[]) => {
      const accepted: File[] = [];
      for (const file of files) {
        if (file.size > MAX_ATTACHMENT_SIZE) {
          toast.error(`文件 ${file.name} 超过 25MB 限制`);
          continue;
        }
        accepted.push(file);
      }
      if (attachments.length + accepted.length > MAX_ATTACHMENT_COUNT) {
        toast.error(`最多同时上传 ${MAX_ATTACHMENT_COUNT} 个附件`);
        accepted.length = Math.max(0, MAX_ATTACHMENT_COUNT - attachments.length);
      }
      if (accepted.length === 0) {
        return;
      }
      try {
        const next = await Promise.all(
          accepted.map(async (file): Promise<ChatAttachment> => ({
            name: file.name,
            type: file.type || "application/octet-stream",
            size: file.size,
            dataUrl: await readFileAsDataUrl(file),
            kind: (file.type || "").startsWith("image/") ? "image" : "file",
          })),
        );
        setAttachments((prev) => [...prev, ...next]);
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取文件失败";
        toast.error(message);
      }
    },
    [attachments.length],
  );

  const handleRemoveAttachment = useCallback((index: number) => {
    setAttachments((prev) => prev.filter((_, currentIndex) => currentIndex !== index));
  }, []);

  const handleSubmit = useCallback(async () => {
    const prompt = input.trim();
    if ((!prompt && attachments.length === 0) || isStreaming) {
      return;
    }

    const now = new Date().toISOString();
    const targetConversation = selectedConversationId
      ? conversationsRef.current.find((item) => item.id === selectedConversationId) ?? null
      : null;
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: prompt,
      createdAt: now,
      status: "done",
      attachments: attachments.length > 0 ? attachments : undefined,
    };
    const assistantMessage: ChatMessage = {
      id: createId(),
      role: "assistant",
      content: "",
      createdAt: now,
      status: "streaming",
      model,
    };
    const conversation: ChatConversation = targetConversation
      ? {
          ...targetConversation,
          model,
          updatedAt: now,
          messages: [...targetConversation.messages, userMessage, assistantMessage],
        }
      : {
          id: createId(),
          title: buildConversationTitle(prompt || attachments[0]?.name || "新对话"),
          model,
          createdAt: now,
          updatedAt: now,
          messages: [userMessage, assistantMessage],
        };

    setInput("");
    setAttachments([]);
    shouldStickToBottomRef.current = true;
    setShowScrollToLatest(false);
    setSelectedConversationId(conversation.id);
    applyConversation(conversation);
    await saveChatConversation(conversation);

    const history = buildCompletionMessages(
      conversation.messages.filter((message) => message.id !== assistantMessage.id),
    );
    void runChatStream(conversation, assistantMessage.id, history);
  }, [applyConversation, attachments, input, isStreaming, model, runChatStream, selectedConversationId]);

  const handleStop = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const handleRegenerate = useCallback(
    async (conversationId: string, messageId: string) => {
      if (isStreaming) {
        return;
      }
      const conversation = conversationsRef.current.find((item) => item.id === conversationId);
      if (!conversation) {
        return;
      }
      const messageIndex = conversation.messages.findIndex((message) => message.id === messageId);
      if (messageIndex === -1) {
        return;
      }

      const now = new Date().toISOString();
      const assistantMessage: ChatMessage = {
        id: createId(),
        role: "assistant",
        content: "",
        createdAt: now,
        status: "streaming",
        model: conversation.model,
      };
      const retainedMessages = conversation.messages.slice(0, messageIndex);
      const nextConversation: ChatConversation = {
        ...conversation,
        updatedAt: now,
        messages: [...retainedMessages, assistantMessage],
      };

      setSelectedConversationId(conversationId);
      shouldStickToBottomRef.current = true;
      applyConversation(nextConversation);
      await saveChatConversation(nextConversation);
      void runChatStream(nextConversation, assistantMessage.id, buildCompletionMessages(retainedMessages));
    },
    [applyConversation, isStreaming, runChatStream],
  );

  const handleCreateDraft = useCallback(() => {
    shouldStickToBottomRef.current = true;
    setShowScrollToLatest(false);
    setSelectedConversationId(null);
    setInput("");
    setAttachments([]);
    textareaRef.current?.focus();
  }, []);

  const handleDeleteConversation = useCallback(async (id: string) => {
    if (streamingConversationId === id) {
      abortControllerRef.current?.abort();
    }
    const nextConversations = conversationsRef.current.filter((item) => item.id !== id);
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    setSelectedConversationId((current) => (current === id ? nextConversations[0]?.id ?? null : current));
    try {
      await deleteChatConversation(id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
    }
  }, [streamingConversationId]);

  const handleClearHistory = useCallback(async () => {
    abortControllerRef.current?.abort();
    try {
      await clearChatConversations();
      conversationsRef.current = [];
      setConversations([]);
      setSelectedConversationId(null);
      setInput("");
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  }, []);

  const handleRenameConversation = useCallback(async (id: string, title: string) => {
    const nextConversations = sortChatConversations(
      conversationsRef.current.map((item) =>
        item.id === id ? { ...item, title, updatedAt: new Date().toISOString() } : item,
      ),
    );
    conversationsRef.current = nextConversations;
    setConversations(nextConversations);
    try {
      await renameChatConversation(id, title);
    } catch (error) {
      const message = error instanceof Error ? error.message : "重命名失败";
      toast.error(message);
    }
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    const target = deleteConfirm;
    setDeleteConfirm(null);
    if (!target) {
      return;
    }
    if (target.type === "all") {
      await handleClearHistory();
      return;
    }
    await handleDeleteConversation(target.id);
  }, [deleteConfirm, handleClearHistory, handleDeleteConversation]);

  return (
    <>
      <section className="mx-auto grid h-[calc(100dvh-6.5rem)] min-h-0 w-full max-w-[1380px] grid-cols-1 gap-2 overflow-hidden px-0 pb-[calc(env(safe-area-inset-bottom)+0.5rem)] sm:h-[calc(100dvh-5.25rem)] sm:gap-3 sm:px-3 sm:pb-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <div className="hidden h-full min-h-0 border-r border-stone-200/70 pr-3 lg:block">
          <ChatSidebar
            conversations={conversations}
            isLoadingHistory={isLoadingHistory}
            selectedConversationId={selectedConversationId}
            onCreateDraft={handleCreateDraft}
            onClearHistory={() => setDeleteConfirm({ type: "all" })}
            onSelectConversation={setSelectedConversationId}
            onDeleteConversation={(id) => setDeleteConfirm({ type: "one", id })}
            onRenameConversation={handleRenameConversation}
            formatConversationTime={formatConversationTime}
          />
        </div>

        <Dialog open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
          <DialogContent className="flex h-[min(82dvh,760px)] w-[92vw] max-w-[460px] flex-col overflow-hidden rounded-[32px] border-white/80 bg-white p-0 shadow-[0_32px_110px_-38px_rgba(15,23,42,0.45)] sm:rounded-[36px]">
            <DialogHeader className="px-6 pt-7 pb-4 sm:px-8">
              <DialogTitle className="flex items-center gap-2 text-xl font-bold tracking-tight">
                <History className="size-5" />
                历史记录
              </DialogTitle>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 sm:px-8">
              <ChatSidebar
                conversations={conversations}
                isLoadingHistory={isLoadingHistory}
                selectedConversationId={selectedConversationId}
                onCreateDraft={() => {
                  handleCreateDraft();
                  setIsHistoryOpen(false);
                }}
                onClearHistory={() => {
                  setIsHistoryOpen(false);
                  setDeleteConfirm({ type: "all" });
                }}
                onSelectConversation={(id) => {
                  setSelectedConversationId(id);
                  setIsHistoryOpen(false);
                }}
                onDeleteConversation={(id) => {
                  setIsHistoryOpen(false);
                  setDeleteConfirm({ type: "one", id });
                }}
                onRenameConversation={handleRenameConversation}
                formatConversationTime={formatConversationTime}
                hideActionButtons
              />
            </div>
          </DialogContent>
        </Dialog>

        <div className="flex min-h-0 flex-col gap-2 sm:gap-4">
          <div className="flex items-center justify-between gap-2 px-1 lg:hidden">
            <Button
              variant="outline"
              className="h-10 flex-1 rounded-2xl border-stone-200 bg-white/90 text-stone-700 shadow-sm"
              onClick={() => setIsHistoryOpen(true)}
            >
              <History className="mr-2 size-4" />
              历史记录 ({conversations.length})
            </Button>
            <Button className="h-10 rounded-2xl bg-stone-950 text-white shadow-sm" onClick={handleCreateDraft}>
              <Plus className="size-4" />
              新建
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-2xl border-stone-200 bg-white/85 px-3 text-stone-600 shadow-sm"
              onClick={() => setDeleteConfirm({ type: "all" })}
              disabled={conversations.length === 0}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>

          <div className="relative min-h-0 flex-1">
            <div
              ref={viewportRef}
              onScroll={handleViewportScroll}
              className="hide-scrollbar h-full overflow-y-auto overscroll-contain px-1 py-2 sm:px-4 sm:py-4"
            >
              <ChatMessages
                conversation={selectedConversation}
                onRegenerate={(conversationId, messageId) => void handleRegenerate(conversationId, messageId)}
                formatConversationTime={formatConversationTime}
              />
            </div>

            {showScrollToLatest ? (
              <button
                type="button"
                aria-label="滚动到最新消息"
                title="滚动到最新消息"
                onClick={() => scrollToLatest("smooth")}
                className="absolute bottom-4 left-1/2 z-20 inline-flex size-11 -translate-x-1/2 items-center justify-center rounded-full border border-stone-200 bg-white/95 text-stone-700 shadow-lg shadow-stone-200/60 backdrop-blur transition hover:-translate-y-0.5 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-stone-400 dark:border-white/10 dark:bg-stone-800/95 dark:text-stone-100 dark:shadow-black/40 dark:hover:bg-stone-700"
              >
                <ArrowDown className="size-5" />
              </button>
            ) : null}
          </div>

          <ChatComposer
            input={input}
            model={model}
            models={models}
            isStreaming={isStreaming}
            attachments={attachments}
            textareaRef={textareaRef}
            fileInputRef={fileInputRef}
            onInputChange={setInput}
            onModelChange={setModel}
            onFilesChange={(files) => void handleFilesChange(files)}
            onRemoveAttachment={handleRemoveAttachment}
            onSubmit={() => void handleSubmit()}
            onStop={handleStop}
          />
        </div>
      </section>

      {deleteConfirm ? (
        <Dialog open onOpenChange={(open) => (!open ? setDeleteConfirm(null) : null)}>
          <DialogContent showCloseButton={false} className="rounded-2xl p-6">
            <DialogHeader className="gap-2">
              <DialogTitle>{deleteConfirm.type === "all" ? "清空历史记录" : "删除对话"}</DialogTitle>
              <DialogDescription className="text-sm leading-6">
                {deleteConfirm.type === "all"
                  ? "确认删除全部对话记录吗？删除后无法恢复。"
                  : "确认删除这条对话吗？删除后无法恢复。"}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                取消
              </Button>
              <Button className="bg-rose-600 text-white hover:bg-rose-700" onClick={() => void handleConfirmDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

export default function ChatPage() {
  const { isCheckingAuth, session } = useAuthGuard();

  if (isCheckingAuth || !session) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <ChatPageContent />;
}
