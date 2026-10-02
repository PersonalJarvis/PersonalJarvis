import { describe, expect, test } from "vitest";
import { googleSignInRejected } from "./browserSignIn";

describe("Google sign-in recovery", () => {
  test.each([
    "https://accounts.google.com/signin/rejected",
    "https://accounts.google.com/v3/signin/rejected?continue=https%3A%2F%2Faccounts.google.com%2Fsignin%2Fchrome%2Fsync%2Ffinish",
    "https://accounts.google.com/v2/signin/rejected/",
  ])("recognizes the rejection page without depending on session parameters: %s", (url) => {
    expect(googleSignInRejected(url)).toBe(true);
  });
  test.each([
    "", "not a URL", "http://accounts.google.com/signin/rejected",
    "https://accounts.google.com.evil.example/signin/rejected",
    "https://accounts.google.com@evil.example/signin/rejected",
    "https://other.example/?continue=https://accounts.google.com/signin/rejected",
    "https://accounts.google.com/signin/v2/identifier",
    "https://accounts.google.com/v3/signin/rejected-other",
    "https://accounts.google.com:8443/signin/rejected",
  ])("does not confuse another page or host with Google's rejection: %s", (url) => {
    expect(googleSignInRejected(url)).toBe(false);
  });
});
