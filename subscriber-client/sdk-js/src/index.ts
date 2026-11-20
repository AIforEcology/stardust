export { AUTH_WARNING, DEFAULT_API_BASE, Stardust, type StardustOptions } from "./client.ts";
export { type Certificate, type DailySummary, type Order, type Quote, StardustError, type Summary } from "./results.ts";
export { detectProvider, extract, fromAnthropic, fromGemini, fromOpenAI, type Provider, type Usage } from "./extract.ts";
export { instrument } from "./instrument.ts";
export { Call, eventIdForSpan, EVENT_ID_NAMESPACE } from "./otel.ts";
