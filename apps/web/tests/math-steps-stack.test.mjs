import { describe, expect, test } from "bun:test";

import { quadraticEquation, restoredChecks, stackPrevious } from "../lib/assignments/mathSteps.ts";

describe("quadraticEquation", () => {
  // Expected strings come from the API's stack_v1.quadratic_equation().
  test("matches the server's canonical equation", () => {
    expect(quadraticEquation(1, -5, 6)).toBe("x^2-5*x+6=0");
    expect(quadraticEquation(-1, 0, 4)).toBe("-x^2+4=0");
    expect(quadraticEquation(2, 1, 0)).toBe("2*x^2+x=0");
    expect(quadraticEquation(4, -4, 1)).toBe("4*x^2-4*x+1=0");
    expect(quadraticEquation(1, -1, 0)).toBe("x^2-x=0");
  });
});

describe("stack chain", () => {
  test("previous carries the equation and every earlier step", () => {
    expect(stackPrevious("x^2-5*x+6=0", [])).toBe("x^2-5*x+6=0");
    expect(stackPrevious("x^2-5*x+6=0", ["(x-2)*(x-3)=0"])).toBe("x^2-5*x+6=0\n(x-2)*(x-3)=0");
  });

  const result = (status, terminal = false) => ({ status, feedback: "", example: null, terminal });

  test("one chain result restores every line", () => {
    const checks = restoredChecks(2, result("valid", true));
    expect(checks.map((c) => c.status)).toEqual(["valid", "valid"]);
    expect(checks.map((c) => c.terminal)).toEqual([false, true]);
    expect(restoredChecks(2, result("invalid")).map((c) => c.status)).toEqual(["valid", "invalid"]);
    expect(restoredChecks(1, result("unsupported")).map((c) => c.status)).toEqual(["unsupported"]);
  });

  test("a checker outage marks nothing", () => {
    expect(restoredChecks(2, result("check_failed"))).toEqual([null, null]);
    expect(restoredChecks(0, result("valid"))).toEqual([]);
  });
});
