"use client";

import { useCallback, useMemo, useState } from "react";
import { Bot, Check, Copy, FileText, LoaderCircle, RefreshCw, TriangleAlert, User } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import type { ChatAttachment, ChatConversation, ChatMessage } from "@/store/chat-conversations";

type ChatMessagesProps = {
  conversation: ChatConversation | null;
  onRegenerate: (conversationId: string, messageId: string) => void;
  formatConversationTime: (value: string) => string;
};

type ContentSegment =
  | { type: "text"; value: string }
  | { type: "code"; language: string; value: string };

const MARKDOWN_LINK = /\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g;
const BARE_URL = /(https?:\/\/[^\s)]+)/g;

function LinkedText({ value }: { value: string }) {
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;

  const pushPlain = (text: string) => {
    if (!text) {
      return;
    }
    // Linkify bare URLs inside the plain run.
    let plainIndex = 0;
    let bare: RegExpExecArray | null;
    BARE_URL.lastIndex = 0;
    while ((bare = BARE_URL.exec(text)) !== null) {
      if (bare.index > plainIndex) {
        nodes.push(text.slice(plainIndex, bare.index));
      }
      const url = bare[1];
      nodes.push(
        <a
          key={`u${key++}`}
          href={url}
          target="_blank"
          rel="noreferrer"
          className="text-sky-600 underline decoration-sky-400/50 underline-offset-2 hover:text-sky-500 dark:text-sky-400"
        >
          {url}
        </a>,
      );
      plainIndex = bare.index + bare[0].length;
    }
    if (plainIndex < text.length) {
      nodes.push(text.slice(plainIndex));
    }
  };

  let match: RegExpExecArray | null;
  MARKDOWN_LINK.lastIndex = 0;
  while ((match = MARKDOWN_LINK.exec(value)) !== null) {
    pushPlain(value.slice(lastIndex, match.index));
    const label = match[1] || match[2];
    nodes.push(
      <a
        key={`l${key++}`}
        href={match[2]}
        target="_blank"
        rel="noreferrer"
        className="font-medium text-sky-600 underline decoration-sky-400/60 underline-offset-2 hover:text-sky-500 dark:text-sky-400"
      >
        {label}
      </a>,
    );
    lastIndex = match.index + match[0].length;
  }
  pushPlain(value.slice(lastIndex));

  return <>{nodes}</>;
}

function splitContentSegments(content: string): ContentSegment[] {
  const segments: ContentSegment[] = [];
  const fence = /```([\w+-]*)\n?([\s\S]*?)(?:```|$)/g;
  let lastIndex = 0;
  let match = fence.exec(content);
  while (match) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", value: content.slice(lastIndex, match.index) });
    }
    segments.push({ type: "code", language: match[1] || "", value: match[2].replace(/\n$/, "") });
    lastIndex = match.index + match[0].length;
    match = fence.exec(content);
  }
  if (lastIndex < content.length) {
    segments.push({ type: "text", value: content.slice(lastIndex) });
  }
  return segments;
}

function MessageContent({ message }: { message: ChatMessage }) {
  const segments = useMemo(() => splitContentSegments(message.content), [message.content]);

  if (!message.content && message.status === "streaming") {
    return (
      <div className="flex items-center gap-2 text-sm text-stone-400">
        <LoaderCircle className="size-3.5 animate-spin" />
        正在思考...
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {segments.map((segment, index) =>
        segment.type === "code" ? (
          <pre
            key={index}
            className="overflow-x-auto rounded-xl bg-stone-950 px-4 py-3 font-mono text-[12.5px] leading-6 text-stone-100 dark:bg-black/60"
          >
            {segment.language ? (
              <div className="mb-1 select-none text-[11px] uppercase tracking-wide text-stone-500">{segment.language}</div>
            ) : null}
            <code>{segment.value}</code>
          </pre>
        ) : (
          <div key={index} className="whitespace-pre-wrap break-words text-[14.5px] leading-7">
            <LinkedText value={segment.value} />
          </div>
        ),
      )}
      {message.status === "streaming" ? (
        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-stone-400 align-middle" />
      ) : null}
    </div>
  );
}

function formatAttachmentSize(size: number) {
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size} B`;
}

function MessageAttachments({ attachments, isUser }: { attachments: ChatAttachment[]; isUser: boolean }) {
  return (
    <div className={cn("flex flex-wrap gap-2", isUser ? "justify-end" : "")}>
      {attachments.map((attachment, index) =>
        attachment.kind === "image" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={`${attachment.name}-${index}`}
            src={attachment.dataUrl}
            alt={attachment.name}
            className="max-h-44 max-w-[220px] rounded-xl object-cover ring-1 ring-stone-200/70 dark:ring-white/10"
          />
        ) : (
          <a
            key={`${attachment.name}-${index}`}
            href={attachment.dataUrl}
            download={attachment.name}
            className="flex items-center gap-2 rounded-xl bg-white px-3 py-2 shadow-sm ring-1 ring-stone-200/70 transition hover:ring-stone-300 dark:bg-white/5 dark:ring-white/10 dark:hover:ring-white/20"
          >
            <div className="flex size-8 items-center justify-center rounded-lg bg-stone-100 text-stone-500 dark:bg-white/10 dark:text-stone-300">
              <FileText className="size-4" />
            </div>
            <div className="min-w-0 max-w-[180px]">
              <div className="truncate text-[12.5px] font-medium text-stone-700 dark:text-stone-200">{attachment.name}</div>
              <div className="text-[11px] text-stone-400">{formatAttachmentSize(attachment.size)}</div>
            </div>
          </a>
        ),
      )}
    </div>
  );
}

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("复制失败");
    }
  }, [content]);

  return (
    <button
      type="button"
      onClick={() => void handleCopy()}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-stone-400 transition hover:bg-stone-100 hover:text-stone-600 dark:hover:bg-white/10 dark:hover:text-stone-200"
      aria-label="复制内容"
    >
      {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

export function ChatMessages({ conversation, onRegenerate, formatConversationTime }: ChatMessagesProps) {
  if (!conversation || conversation.messages.length === 0) {
    return (
      <div className="flex h-full min-h-[40vh] flex-col items-center justify-center gap-3 text-center">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-stone-100 text-stone-400 dark:bg-white/10 dark:text-stone-500">
          <Bot className="size-6" />
        </div>
        <div className="text-base font-semibold text-stone-700 dark:text-stone-200">开始新的对话</div>
        <div className="max-w-[320px] text-sm leading-6 text-stone-400">
          在下方输入消息即可开始，对话记录会保存在本地浏览器中。
        </div>
      </div>
    );
  }

  const lastAssistantId = [...conversation.messages].reverse().find((message) => message.role === "assistant")?.id;

  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-5 pb-4">
      {conversation.messages.map((message) => {
        const isUser = message.role === "user";
        return (
          <div key={message.id} className={cn("flex gap-3", isUser ? "flex-row-reverse" : "")}>
            <div
              className={cn(
                "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl",
                isUser
                  ? "bg-stone-950 text-white dark:bg-white dark:text-stone-950"
                  : "bg-stone-100 text-stone-500 dark:bg-white/10 dark:text-stone-300",
              )}
            >
              {isUser ? <User className="size-4" /> : <Bot className="size-4" />}
            </div>
            <div className={cn("flex min-w-0 max-w-[86%] flex-col gap-1.5", isUser ? "items-end" : "items-start")}>
              {message.attachments?.length ? (
                <MessageAttachments attachments={message.attachments} isUser={isUser} />
              ) : null}
              {message.content || message.status !== "done" ? (
                <div
                  className={cn(
                    "rounded-2xl px-4 py-2.5",
                    isUser
                      ? "bg-stone-950 text-white dark:bg-white dark:text-stone-950"
                      : "bg-white text-stone-900 shadow-sm ring-1 ring-stone-200/70 dark:bg-white/5 dark:text-stone-100 dark:ring-white/10",
                  )}
                >
                  <MessageContent message={message} />
                  {message.status === "error" ? (
                    <div className="mt-2 flex items-start gap-1.5 rounded-lg bg-rose-50 px-2.5 py-1.5 text-[12.5px] leading-5 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300">
                      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                      {message.error || "回复失败"}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <div
                className={cn(
                  "flex items-center gap-1 px-1 text-[11px] text-stone-300 dark:text-stone-600",
                  isUser ? "flex-row-reverse" : "",
                )}
              >
                <span>{formatConversationTime(message.createdAt)}</span>
                {!isUser && message.model ? <span>· {message.model}</span> : null}
                {message.content ? <CopyButton content={message.content} /> : null}
                {!isUser && message.id === lastAssistantId && message.status !== "streaming" ? (
                  <button
                    type="button"
                    onClick={() => onRegenerate(conversation.id, message.id)}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-stone-400 transition hover:bg-stone-100 hover:text-stone-600 dark:hover:bg-white/10 dark:hover:text-stone-200"
                  >
                    <RefreshCw className="size-3.5" />
                    重新生成
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
