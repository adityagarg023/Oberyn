import type { EventV1, JsonValue } from "../../contracts/src/events.js";

const SENSITIVE_KEY = /(?:password|passwd|pwd|token|secret|authorization|cookie|api[-_]?key|access[-_]?key|client[-_]?secret|credit[-_]?card|card[-_]?(?:number|no|cvv|cvc|expiry)|\bcvv\b|\bcvc\b|\bpan\b)/i;
const BEARER = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+\/-]+=*/gi;
const ASSIGNMENT = /\b(password|passwd|token|access_token|refresh_token|api_key|authorization|client_secret)\s*([=:])\s*([^\s&,;]+)/gi;
const URL_PARAM = /([?&](?:password|token|access_token|refresh_token|api_key|authorization|secret)=)[^&#\s]*/gi;
const CARD_CANDIDATE = /\b(?:\d[ -]*?){13,19}\b/g;

function passesLuhn(value: string): boolean {
  const digits = value.replace(/\D/g, "");
  if (digits.length < 13 || digits.length > 19) return false;
  let sum = 0;
  let doubleDigit = false;
  for (let i = digits.length - 1; i >= 0; i -= 1) {
    let digit = Number(digits[i]);
    if (doubleDigit) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    doubleDigit = !doubleDigit;
  }
  return sum % 10 === 0;
}

export function scrubString(value: string): string {
  return value
    .replace(BEARER, "$1 [REDACTED]")
    .replace(ASSIGNMENT, "$1$2[REDACTED]")
    .replace(URL_PARAM, "$1[REDACTED]")
    .replace(CARD_CANDIDATE, (candidate) => passesLuhn(candidate) ? "[REDACTED_CARD]" : candidate);
}

function scrubValue(value: unknown, depth = 0): JsonValue {
  if (depth > 20) return "[Truncated]";
  if (typeof value === "string") return scrubString(value);
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (Array.isArray(value)) return value.map((item) => scrubValue(item, depth + 1));
  if (typeof value === "object") {
    const result: Record<string, JsonValue> = {};
    for (const [key, child] of Object.entries(value)) {
      result[scrubString(key)] = SENSITIVE_KEY.test(key)
        ? "[REDACTED]"
        : scrubValue(child, depth + 1);
    }
    return result;
  }
  return "[Unsupported]";
}

/** Return a scrubbed copy; the caller's event is never mutated. */
export function scrubEvent<T extends EventV1>(event: T): T {
  return scrubValue(event) as T;
}
