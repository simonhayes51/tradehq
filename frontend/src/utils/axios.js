// src/utils/axios.js
// Consolidated: this file used to be a second, divergent axios instance
// (no retries, no 402 handling, its own 401 redirect). Everything now goes
// through the one canonical client in src/axios.js.
export { default } from "../axios";
