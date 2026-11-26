// Helpers for MATH_STEPS tasks that use the STACK pilot checker (stack_v1).

export const STACK_VERSION = 'stack_v1'
export const MAX_STACK_COEFFICIENT = 1000

export type StepCheck = {
  status: 'valid' | 'invalid' | 'unsupported' | 'check_failed'
  feedback: string
  example?: string | null
  terminal: boolean
}

// Same text as the API's stack_v1.quadratic_equation(): the server rejects a
// learner-facing equation that does not match its coefficients.
// Mirrors the API's quadratic_roots(): discriminant must be a perfect square >= 0.
export function hasRationalRoots(a: number, b: number, c: number): boolean {
  const d = b * b - 4 * a * c
  return d >= 0 && Number.isInteger(Math.sqrt(d))
}

export function quadraticEquation(a: number, b: number, c: number): string {
  const terms: string[] = []
  for (const [coefficient, variable] of [[a, 'x^2'], [b, 'x'], [c, '']] as const) {
    if (!coefficient) continue
    const magnitude = Math.abs(coefficient)
    const term = variable ? (magnitude === 1 ? variable : `${magnitude}*${variable}`) : String(magnitude)
    terms.push((coefficient < 0 ? '-' : terms.length ? '+' : '') + term)
  }
  return `${terms.join('')}=0`
}

// stack_v1 check_step contract: `previous` is the original equation followed
// by every earlier step, one per line; `current` is the step being checked.
export function stackPrevious(equation: string, priorSteps: string[]): string {
  return [equation, ...priorSteps].join('\n')
}

// Per-line status from one whole-chain check of saved steps. A step can only be
// added after a valid one, so every line but the last was valid when added.
// shortcut: a chain saved outside this UI may break earlier, but is shown on the last line; grading re-checks the whole chain anyway.
export function restoredChecks(stepCount: number, result: StepCheck): (StepCheck | null)[] {
  if (!stepCount) return []
  const prior: StepCheck = { status: 'valid', feedback: '', example: null, terminal: false }
  if (result.status === 'check_failed') return Array(stepCount).fill(null)
  return [...Array(stepCount - 1).fill(prior), result]
}
