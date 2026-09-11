import { en } from "./en";

export type MessageKey = keyof typeof en;
export type PluralMessage = {
  other: string;
  zero?: string;
  one?: string;
  two?: string;
  few?: string;
  many?: string;
};
export type Message = string | PluralMessage;
export type Params = Record<string, string | number>;

// Reactive: templates calling t() re-render when the locale changes.
let locale = $state("en");

const catalogs: Record<string, Partial<Record<MessageKey, Message>>> = { en };

export function getLocale(): string {
  return locale;
}

export function setLocale(code: string): void {
  locale = code;
}

export function registerCatalog(
  code: string,
  messages: Partial<Record<MessageKey, Message>>,
): void {
  const existing = catalogs[code];
  catalogs[code] = existing ? { ...existing, ...messages } : messages;
}

export function t(key: MessageKey, params?: Params): string {
  const message = resolve(key);
  if (message === undefined) return key;
  return format(message, params);
}

function resolve(key: MessageKey): Message | undefined {
  const catalog = catalogs[locale];
  if (catalog && Object.hasOwn(catalog, key)) return catalog[key];
  if (Object.hasOwn(en, key)) return en[key];
  return undefined;
}

function format(message: Message, params?: Params): string {
  if (typeof message === "string") return interpolate(message, params);
  const plural = message;
  if (params?.count === undefined) return interpolate(plural.other, params);
  const category = new Intl.PluralRules(locale).select(Number(params.count));
  return interpolate(plural[category] ?? plural.other, params);
}

function interpolate(text: string, params?: Params): string {
  if (params === undefined) return text;
  return text.replace(/\{(\w+)\}/g, (match, name: string) => {
    return Object.hasOwn(params, name) ? String(params[name]) : match;
  });
}
