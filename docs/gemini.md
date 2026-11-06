# Gemini integration

The backend uses Google's native `generateContent` REST API. Set `GEMINI_API_KEY` in the backend environment, optionally override `GEMINI_MODEL` (default `gemini-3.8-flash`), then start `python -m overwatch demo`. No provider SDK is required. `.env.example` documents the variables; the application does not automatically load `.env` files.

Run the frontend separately with `npm ci` and `npm run dev`. In Onboarding, connect the backend URL with an operator or procurement role token. Open Tenders and use the Gemini drafting panel. Without a key the backend explicitly returns an offline stub. Provider keys never belong in browser settings, frontend environment variables or deployment artifacts.

## Contract and permission boundary

`overwatch/gemini.py` owns transport and native function calling. `overwatch/assist.py` owns the bounded tool loop. The three exposed tools read case evidence, discover indicative providers and read recent tenders. They cannot approve, sign, send messages, order parts or execute hardware actions. All model outputs are `DRAFT_ONLY` and `UNVERIFIED_MODEL_DRAFT`; instructions to cite evidence are not a factual verification mechanism. Review claims against returned evidence before using a draft.

The adapter preserves the exact original model Content, including thought signatures, across function-result turns. It maps native function IDs to local tool IDs, sends the key only in the `x-goog-api-key` header, rejects redirects and malformed/truncated responses, and sanitizes HTTP errors. Calls have a 30-second timeout, a 2 MB response cap, 4,096 output-token budget, at most eight calls per turn and four model turns per request. Prompt input is limited to 16,000 characters. Backend users should still budget for paid model and optional Crustdata calls.

## Verification

`python -m unittest tests.test_gemini tests.test_assist -v` uses scripted transports, including a complete model → discovery → model round trip. Tests check signature/ID preservation, header credentials, failure handling, explicit offline mode and the absence of approval tools. These are contract tests, not proof of live account access or model quality. A live smoke requires an authorized Gemini key and quota. Physical hardware acceptance remains a separate release gate.

Official contracts: [Gemini API reference](https://ai.google.dev/api/generate-content), [function calling](https://ai.google.dev/gemini-api/docs/function-calling), [thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures), [models](https://ai.google.dev/gemini-api/docs/models).
