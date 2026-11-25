import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it } from "vitest";
import { apiErrorMessage, GENERIC_ERROR, NETWORK_ERROR } from "./apiError";

const httpError = (status: number, data: unknown) =>
  new AxiosError("Request failed", "ERR_BAD_REQUEST", undefined, undefined, {
    status,
    data,
    statusText: "",
    headers: {},
    config: { headers: new AxiosHeaders() },
  });

describe("apiErrorMessage", () => {
  it("returns the backend detail when it is text", () => {
    expect(apiErrorMessage(httpError(409, { detail: "Ya existe." }))).toBe("Ya existe.");
  });

  it("joins FastAPI validation errors with their field, without the 'body' prefix", () => {
    const data = {
      detail: [
        { loc: ["body", "key"], msg: "String should match pattern" },
        { loc: ["body", "config", "url"], msg: "url es obligatoria" },
      ],
    };
    expect(apiErrorMessage(httpError(422, data))).toBe(
      "key: String should match pattern; config.url: url es obligatoria",
    );
  });

  it("uses only the message when a validation error has no field", () => {
    expect(apiErrorMessage(httpError(422, { detail: [{ loc: ["body"], msg: "Valor no válido" }] }))).toBe(
      "Valor no válido",
    );
  });

  it("falls back to the generic message when there is no usable detail", () => {
    expect(apiErrorMessage(httpError(500, "Internal Server Error"))).toBe(GENERIC_ERROR);
    expect(apiErrorMessage(httpError(400, { detail: "   " }))).toBe(GENERIC_ERROR);
  });

  it("reports a network error when there is no response", () => {
    expect(apiErrorMessage(new AxiosError("Network Error", "ERR_NETWORK"))).toBe(NETWORK_ERROR);
  });

  it("returns the generic message for anything that is not an axios error", () => {
    expect(apiErrorMessage(new Error("boom"))).toBe(GENERIC_ERROR);
    expect(apiErrorMessage(undefined)).toBe(GENERIC_ERROR);
  });
});
