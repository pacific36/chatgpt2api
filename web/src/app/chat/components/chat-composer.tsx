"use client";

import type { RefObject } from "react";
import { ArrowUp, FileText, Paperclip, Square, X } from "lucide-react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ChatAttachment } from "@/store/chat-conversations";

export const CHAT_ACCEPT_FILE_TYPES =
  "image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json";

type ChatComposerProps = {
  input: string;
  model: string;
  models: string[];
  isStreaming: boolean;
  attachments: ChatAttachment[];
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onInputChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onFilesChange: (files: File[]) => void;
  onRemoveAttachment: (index: number) => void;
  onSubmit: () => void;
  onStop: () => void;
};

function formatFileSize(size: number) {
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${Math.round(size / 1024)} KB`;
  }
  return `${size} B`;
}

export function ChatComposer({
  input,
  model,
  models,
  isStreaming,
  attachments,
  textareaRef,
  fileInputRef,
  onInputChange,
  onModelChange,
  onFilesChange,
  onRemoveAttachment,
  onSubmit,
  onStop,
}: ChatComposerProps) {
  const canSubmit = (input.trim().length > 0 || attachments.length > 0) && !isStreaming;

  return (
    <div className="mx-auto w-full max-w-[820px] px-1 sm:px-0">
      <div className="rounded-[22px] border border-stone-200/80 bg-white/95 p-2.5 shadow-[0_18px_60px_-30px_rgba(15,23,42,0.35)] backdrop-blur dark:border-white/10 dark:bg-stone-900/80">
        {attachments.length > 0 ? (
          <div className="hide-scrollbar flex gap-2 overflow-x-auto px-1 pb-2">
            {attachments.map((attachment, index) => (
              <div
                key={`${attachment.name}-${index}`}
                className="group relative flex shrink-0 items-center gap-2 rounded-xl border border-stone-200 bg-stone-50 px-2 py-1.5 dark:border-white/10 dark:bg-white/5"
              >
                {attachment.kind === "image" ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={attachment.dataUrl}
                    alt={attachment.name}
                    className="size-9 rounded-lg object-cover"
                  />
                ) : (
                  <div className="flex size-9 items-center justify-center rounded-lg bg-stone-200/70 text-stone-500 dark:bg-white/10 dark:text-stone-300">
                    <FileText className="size-4.5" />
                  </div>
                )}
                <div className="min-w-0 max-w-[140px]">
                  <div className="truncate text-[12px] font-medium text-stone-700 dark:text-stone-200">{attachment.name}</div>
                  <div className="text-[10.5px] text-stone-400">{formatFileSize(attachment.size)}</div>
                </div>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(index)}
                  className="absolute -top-1.5 -right-1.5 inline-flex size-4.5 items-center justify-center rounded-full bg-stone-900 text-white opacity-0 transition group-hover:opacity-100 dark:bg-white dark:text-stone-900"
                  aria-label="移除附件"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          value={input}
          rows={2}
          placeholder="输入消息，Enter 发送，Shift + Enter 换行"
          onChange={(event) => onInputChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSubmit) {
                onSubmit();
              }
            }
          }}
          className="hide-scrollbar max-h-44 w-full resize-none bg-transparent px-2 py-1.5 text-[14.5px] leading-6 text-stone-900 outline-none placeholder:text-stone-400 dark:text-stone-100"
        />
        <div className="flex items-center justify-between gap-2 px-1 pt-1">
          <div className="flex items-center gap-1.5">
            <Select value={model} onValueChange={onModelChange}>
              <SelectTrigger className="h-9 w-auto min-w-[120px] rounded-xl border-stone-200 bg-white text-[13px] shadow-none dark:border-white/10 dark:bg-transparent">
                <SelectValue placeholder="模型" />
              </SelectTrigger>
              <SelectContent className="z-[120]">
                {models.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isStreaming}
              className="inline-flex size-9 items-center justify-center rounded-xl text-stone-500 transition hover:bg-stone-100 hover:text-stone-700 disabled:cursor-not-allowed disabled:opacity-40 dark:text-stone-400 dark:hover:bg-white/10 dark:hover:text-stone-200"
              aria-label="上传文件"
              title="上传图片或文档（PDF / Word / 文本等）"
            >
              <Paperclip className="size-4.5" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={CHAT_ACCEPT_FILE_TYPES}
              className="hidden"
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                if (files.length > 0) {
                  onFilesChange(files);
                }
                event.target.value = "";
              }}
            />
          </div>
          {isStreaming ? (
            <button
              type="button"
              onClick={onStop}
              className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-stone-100 px-4 text-[13px] font-medium text-stone-700 transition hover:bg-stone-200 dark:bg-white/10 dark:text-stone-200 dark:hover:bg-white/15"
            >
              <Square className="size-3.5 fill-current" />
              停止
            </button>
          ) : (
            <button
              type="button"
              onClick={onSubmit}
              disabled={!canSubmit}
              className="inline-flex size-9 items-center justify-center rounded-xl bg-stone-950 text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:opacity-35 dark:bg-white dark:text-stone-950 dark:hover:bg-stone-200"
              aria-label="发送"
            >
              <ArrowUp className="size-4.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
