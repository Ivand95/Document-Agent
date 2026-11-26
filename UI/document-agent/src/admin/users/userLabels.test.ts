import { describe, expect, it } from "vitest";
import { displayName, sourceLabel } from "./userLabels";

describe("sourceLabel", () => {
  it.each([
    ["assigned", "Asignado a mano"],
    ["everyone", "Todos los usuarios"],
    ["department:Marketing", "Por departamento Marketing"],
    ["position_prefix:GR -", "Por puesto GR -"],
    ["legacy:ADMIN_DEPARTMENT", "Configuración antigua (ADMIN_DEPARTMENT)"],
    ["something:else", "something:else"],
  ])("%s → %s", (source, label) => {
    expect(sourceLabel(source)).toBe(label);
  });
});

describe("displayName", () => {
  it("uses the trimmed name", () => {
    expect(displayName({ name: "  Ana Pérez ", email: "ana@x.com" })).toBe("Ana Pérez");
  });

  it("falls back to the email, then to a placeholder", () => {
    expect(displayName({ name: "  ", email: "ana@x.com" })).toBe("ana@x.com");
    expect(displayName({ name: null, email: null })).toBe("(sin nombre)");
  });
});
