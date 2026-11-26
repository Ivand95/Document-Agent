import { describe, expect, it } from "vitest";
import {
  defaultStatusTab,
  fixedDepartment,
  parseUsersSearch,
  toSearchParams,
  toUsersQuery,
  type UsersSearch,
} from "./usersSearch";

describe("parseUsersSearch", () => {
  it("reads every field", () => {
    expect(parseUsersSearch(new URLSearchParams("status=disabled&q=ana&department=Marketing&page=3"))).toEqual({
      status: "disabled",
      q: "ana",
      department: "Marketing",
      page: 3,
    });
  });

  it("uses defaults for missing or invalid values", () => {
    expect(parseUsersSearch(new URLSearchParams("status=weird&page=-2"))).toEqual({
      status: null,
      q: "",
      department: "",
      page: 1,
    });
  });
});

describe("toSearchParams", () => {
  it("omits default values", () => {
    expect(toSearchParams({ status: null, q: "", department: "", page: 1 }).toString()).toBe("");
  });

  it("round-trips with parseUsersSearch", () => {
    const search: UsersSearch = { status: "all", q: "luis", department: "Comercial", page: 2 };
    expect(parseUsersSearch(toSearchParams(search))).toEqual(search);
  });
});

describe("toUsersQuery", () => {
  it("maps the tab, the trimmed search, the department and the page", () => {
    expect(toUsersQuery({ status: "pending", q: "  ana ", department: "Marketing", page: 2 })).toEqual({
      status: "pending",
      q: "ana",
      department: "Marketing",
      limit: 50,
      offset: 50,
    });
  });

  it("sends no status for 'all' or an unchosen tab, and no empty filters", () => {
    expect(toUsersQuery({ status: "all", q: " ", department: "", page: 1 })).toEqual({ limit: 50, offset: 0 });
    expect(toUsersQuery({ status: null, q: "", department: "", page: 1 })).toEqual({ limit: 50, offset: 0 });
  });
});

describe("defaultStatusTab", () => {
  it("opens on pending when there are pending users", () => {
    expect(defaultStatusTab({ pending: 2, active: 5, disabled: 0, all: 7 })).toBe("pending");
  });

  it("opens on all otherwise", () => {
    expect(defaultStatusTab({ pending: 0, active: 5, disabled: 1, all: 6 })).toBe("all");
  });
});

describe("fixedDepartment", () => {
  it("is the only department of a limited scope", () => {
    expect(fixedDepartment({ limited: true, departments: ["Marketing"] })).toBe("Marketing");
  });

  it("is null for several departments, none, or a global scope", () => {
    expect(fixedDepartment({ limited: true, departments: ["Marketing", "Comercial"] })).toBeNull();
    expect(fixedDepartment({ limited: true, departments: [] })).toBeNull();
    expect(fixedDepartment({ limited: false, departments: ["Marketing"] })).toBeNull();
  });
});
