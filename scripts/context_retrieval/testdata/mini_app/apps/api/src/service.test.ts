import { greet } from "./service";

describe("greet", () => {
  it("works", () => {
    expect(greet("a")).toContain("a");
  });
});
