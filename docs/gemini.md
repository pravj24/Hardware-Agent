# Gemini integration

The backend uses Google's native `generateContent` REST API. Set `GEMINI_API_KEY` in the backend environment, optionally override `GEMINI_MODEL` (default `gemini-3.8-flash`), then start `python -m overwatch demo`. No provider SDK is required. Alternatively, copy `.env.example` to `.env` in the working directory and fill in the key locally. Demo startup loads only the supported backend variables; existing environment values take precedence, including explicitly empty values. Quotes are optional; values are literal, with no shell expansion or interpolation. `.env` stays ignored by Git.

GitHub stores the key under the Actions repository secret `GEMINI_API_KEY`. A deployment workflow must explicitly pass that secret into the Python service's runtime environment; GitHub does not automatically configure a separately hosted backend. Ordinary test/frontend build jobs do not receive it and make no paid calls. Never place the value directly in workflow YAML or frontend build variables. To replace the key, update the repository secret and local `.env` separately.

Run the frontend separately with `npm ci` and `npm run dev`. In Onboarding, connect the backend URL with an operator or procurement role token. Open Tenders and use the Gemini drafting panel. Without a key the backend explicitly returns an offline stub. Provider keys never belong in browser settings, frontend environment variables or deployment artifacts.

## Contract and permission boundary

`overwatch/gemini.py` owns transport and native function calling. `overwatch/assist.py` owns the bounded tool loop. The three exposed tools read case evidence, discover indicative providers and read recent tenders. They cannot approve, sign, send messages, order parts or execute hardware actions. All model outputs are `DRAFT_ONLY` and `UNVERIFIED_MODEL_DRAFT`; instructions to cite evidence are not a factual verification mechanism. Review claims against returned evidence before using a draft.

The adapter preserves the exact original model Content, including thought signatures, across function-result turns. It maps native function IDs to local tool IDs, sends the key only in the `x-goog-api-key` header, rejects redirects and malformed/truncated responses, and sanitizes HTTP errors. Calls have a 30-second timeout, a 2 MB response cap, 4,096 output-token budget, at most eight calls per turn and four model turns per request. Prompt input is limited to 16,000 characters. Backend users should still budget for paid model and optional Crustdata calls.

## Verification

`python -m unittest tests.test_gemini tests.test_assist -v` uses scripted transports, including a complete model → discovery → model round trip. Tests check signature/ID preservation, header credentials, failure handling, explicit offline mode and the absence of approval tools. These are contract tests, not proof of live account access or model quality. A live smoke requires an authorized Gemini key and quota. Physical hardware acceptance remains a separate release gate.

Official contracts: [Gemini API reference](https://ai.google.dev/api/generate-content), [function calling](https://ai.google.dev/gemini-api/docs/function-calling), [thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures), [models](https://ai.google.dev/gemini-api/docs/models).
