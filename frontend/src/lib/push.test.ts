import { describe, expect, it } from "vitest";
import { endpointKey, keyToBytes, sameKey } from "./push";

// 웹 푸시 서버 공개키(65바이트 P-256 점, base64url)
const KEY = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8";

describe("keyToBytes", () => {
  it("base64url 공개키를 65바이트로", () => {
    const bytes = keyToBytes(KEY);
    expect(bytes.length).toBe(65);
    expect(bytes[0]).toBe(4);
  });

  it("- 와 _ 를 쓰고 = 가 없어도 읽는다", () => {
    expect(Array.from(keyToBytes("-_8"))).toEqual([0xfb, 0xff]);
    expect(Array.from(keyToBytes("AQID"))).toEqual([1, 2, 3]);
  });
});

describe("sameKey", () => {
  it("구독이 쓴 키와 서버 키가 같을 때만 참", () => {
    const key = keyToBytes(KEY);
    expect(sameKey(key.slice().buffer, key)).toBe(true);
    const other = key.slice();
    other[10] ^= 1;
    expect(sameKey(other.buffer, key)).toBe(false);
    expect(sameKey(key.slice(0, 64).buffer, key)).toBe(false);
    expect(sameKey(null, key)).toBe(false);
  });
});

describe("endpointKey", () => {
  it("서버(push_devices.key)와 같은 SHA-256 hex", async () => {
    // sha256("https://fcm.googleapis.com/fcm/send/abc")
    expect(await endpointKey("https://fcm.googleapis.com/fcm/send/abc")).toBe(
      "4e9bdab8bbe7189c00aebb0922fc49fe40e00594c400f3e1b5dcf97a35c79f85",
    );
  });
});
