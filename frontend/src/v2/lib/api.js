// src/v2/lib/api.js
//
// Reuses the existing, already-correct axios instance (retries, 402
// premium:blocked event dispatch, credentials, base URL resolution) -
// this is generic HTTP-client infrastructure with no v1 UI coupling, so
// v2 calls it directly instead of building a second one.
export { default as api } from "../../axios";
