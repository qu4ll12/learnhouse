'use client'
import { useAssignments } from '@components/Contexts/Assignments/AssignmentContext'
import { useAssignmentSubmission } from '@components/Contexts/Assignments/AssignmentSubmissionContext'
import {
  useAssignmentsTask,
  useAssignmentsTaskDispatch,
} from '@components/Contexts/Assignments/AssignmentsTaskContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import AssignmentBoxUI from '@components/Objects/Activities/Assignment/AssignmentBoxUI'
import { answerInputClass, answerKeyPanelClass } from '@components/Objects/Activities/Assignment/assignmentUI'
import {
  checkMathStep,
  getAssignmentTask,
  getAssignmentTaskSubmissionsMe,
  getAssignmentTaskSubmissionsUser,
  handleAssignmentTaskSubmission,
  updateAssignmentTask,
} from '@services/courses/assignments'
import { SegmentedControl } from '@/design'
import {
  MAX_STACK_COEFFICIENT,
  STACK_VERSION,
  quadraticEquation,
  restoredChecks,
  stackPrevious,
  type StepCheck,
} from '@/lib/assignments/mathSteps'
import { ArrowClockwise, ArrowCounterClockwise, CheckCircle, Question, WarningCircle, XCircle } from '@phosphor-icons/react'
import React, { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { applyManualGrade } from './applyManualGrade'

// Step-by-step equation solving. Linear tasks use the MATHCDT checker; tasks
// with checker_version stack_v1 (quadratic pilot) are checked by STACK, which
// needs the whole chain. Each step is checked live (POST .../check_step); the
// grade is all-or-nothing and re-computed server-side from the saved steps
// (_server_verified_task_grade), so these live checks are feedback only.

type MathStepsContents = { equation: string; explanation?: string; checker_version?: string }
type Degree = 'linear' | 'quadratic'

type TaskMathStepsObjectProps = {
  view: 'teacher' | 'student' | 'grading'
  assignmentTaskUUID?: string
  user_id?: string
  onGraded?: () => void
}

const STATUS_STYLE: Record<StepCheck['status'], { Icon: any; className: string }> = {
  valid: { Icon: CheckCircle, className: 'text-emerald-600' },
  invalid: { Icon: XCircle, className: 'text-rose-600' },
  unsupported: { Icon: Question, className: 'text-amber-600' },
  check_failed: { Icon: WarningCircle, className: 'text-neutral-500' },
}

function parseCoefficients(raw: string[]): [number, number, number] | null {
  const values = raw.map((v) => (v.trim() === '' ? NaN : Number(v)))
  const ok = values.every((v) => Number.isInteger(v) && Math.abs(v) <= MAX_STACK_COEFFICIENT) && values[0] !== 0
  return ok ? (values as [number, number, number]) : null
}

function savedSteps(raw: any): string[] {
  const steps = raw?.task_submission?.steps
  return Array.isArray(steps) ? steps.filter((s: any) => typeof s === 'string') : []
}

function TaskMathStepsObject({ view, assignmentTaskUUID, user_id, onGraded }: TaskMathStepsObjectProps) {
  const { t } = useTranslation()
  const tk = (key: string) => t(`dashboard.assignments.editor.task_editor.math_steps.${key}`)
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const assignmentTaskState = useAssignmentsTask() as any
  const assignmentTaskStateHook = useAssignmentsTaskDispatch() as any
  const assignment = useAssignments() as any
  const assignmentUUID = assignment?.assignment_object?.assignment_uuid
  const queryClient = useQueryClient()
  const assignmentSubmission = useAssignmentSubmission() as any
  const submissionStatus = Array.isArray(assignmentSubmission) && assignmentSubmission.length > 0
    ? assignmentSubmission[0].submission_status
    : null
  const answersLocked = submissionStatus === 'GRADED' || submissionStatus === 'SUBMITTED'
  const showCorrectAnswers = view === 'student'
    && submissionStatus === 'GRADED'
    && !!assignment?.assignment_object?.show_correct_answers

  const [contents, setContents] = useState<MathStepsContents>({ equation: '', explanation: '' })
  const [equationError, setEquationError] = useState<string | null>(null)
  const [degree, setDegree] = useState<Degree>('linear')
  const [coefficients, setCoefficients] = useState<string[]>(['', '', ''])
  // The checker could not be reached (or the saved chain could not be re-checked).
  const [serviceError, setServiceError] = useState<'step' | 'restore' | null>(null)
  const [steps, setSteps] = useState<string[]>([])
  const [initialSteps, setInitialSteps] = useState<string[]>([])
  const [checks, setChecks] = useState<(StepCheck | null)[]>([])
  const [draft, setDraft] = useState('')
  const [checking, setChecking] = useState(false)
  // Once the learner edits, stop re-hydrating from the server (same guard as
  // the other task types) so a late response can't clobber their work.
  const interactedRef = React.useRef(false)

  const [userSubmissions, setUserSubmissions] = useState<any>(null)
  const [userSubmissionObject, setUserSubmissionObject] = useState<any>(null)
  const [taskOutsideProvider, setTaskOutsideProvider] = useState<any>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const taskUUID = assignmentTaskUUID || assignmentTaskState?.assignmentTask?.assignment_task_uuid

  const isStack = contents.checker_version === STACK_VERSION

  async function check(previous: string, current: string): Promise<StepCheck | null> {
    const res = await checkMathStep(previous, current, taskUUID, assignmentUUID, access_token)
    return res.success ? res.data : null
  }

  // What `previous` must carry for the next step: the last line for the
  // linear checker, the whole chain so far for STACK.
  function previousFor(c: MathStepsContents, prior: string[]) {
    if (c.checker_version === STACK_VERSION) return stackPrevious(c.equation, prior)
    return prior.length ? prior[prior.length - 1] : c.equation
  }

  // Re-derive every line's status for saved steps (they aren't stored). STACK
  // gets a single whole-chain check rather than one request per line.
  async function checkAll(c: MathStepsContents, list: string[]) {
    if (c.checker_version !== STACK_VERSION) {
      const chain = [c.equation, ...list]
      const results = await Promise.all(list.map((step, i) => check(chain[i], step)))
      setServiceError(results.some((r) => !r || r.status === 'check_failed') ? 'restore' : null)
      setChecks(results)
      return
    }
    setChecking(true)
    const result = await check(previousFor(c, list.slice(0, -1)), list[list.length - 1])
    setChecking(false)
    const failed = !result || result.status === 'check_failed'
    setServiceError(failed ? 'restore' : null)
    setChecks(failed ? list.map(() => null) : restoredChecks(list.length, result))
  }

  // --- TEACHER VIEW ---
  useEffect(() => {
    if (view === 'teacher' && assignmentTaskState?.assignmentTask?.contents) {
      const c = assignmentTaskState.assignmentTask.contents
      const stack = c.checker_version === STACK_VERSION && Array.isArray(c.stack_coefficients)
      // A STACK equation is not a linear one: switching type starts a blank equation.
      /* eslint-disable-next-line react-hooks/set-state-in-effect */
      setContents({ equation: stack ? '' : c.equation ?? '', explanation: c.explanation ?? '' })
      setDegree(stack ? 'quadratic' : 'linear')
      setCoefficients(stack ? c.stack_coefficients.map(String) : ['', '', ''])
    }
  }, [view, assignmentTaskState])

  // --- STUDENT / GRADING VIEW ---
  useEffect(() => {
    if (view === 'teacher' || !assignmentTaskUUID || !assignmentUUID || (view === 'grading' && !user_id)) return
    let cancelled = false
    ;(async () => {
      const taskRes = await getAssignmentTask(assignmentTaskUUID, access_token)
      if (cancelled || !taskRes.success) return
      const raw = taskRes.data.contents || {}
      const c = { equation: raw.equation ?? '', explanation: raw.explanation ?? '', checker_version: raw.checker_version }
      setTaskOutsideProvider(taskRes.data)
      setContents(c)
      const subRes = view === 'student'
        ? await getAssignmentTaskSubmissionsMe(assignmentTaskUUID, assignmentUUID, access_token)
        : await getAssignmentTaskSubmissionsUser(assignmentTaskUUID, String(user_id), assignmentUUID, access_token)
      if (cancelled || !subRes.success || !subRes.data) return
      setUserSubmissions(subRes.data)
      if (view === 'grading') setUserSubmissionObject(subRes.data)
      const saved = savedSteps(subRes.data)
      setInitialSteps(saved)
      if (!interactedRef.current) {
        setSteps(saved)
        if (c.equation && saved.length) checkAll(c, saved)
      }
    })()
    return () => { cancelled = true }
  }, [view, assignmentTaskUUID, assignmentUUID, user_id, access_token, reloadKey])

  // --- SAVE (teacher) ---
  async function saveFC() {
    if (!assignmentTaskState?.assignmentTask?.assignment_task_uuid) return
    // The server validates both kinds (supported linear equation / STACK pilot
    // range) and its message is shown as-is.
    let saved: MathStepsContents & { stack_coefficients?: number[] }
    if (degree === 'quadratic') {
      const abc = parseCoefficients(coefficients)
      if (!abc) {
        setEquationError(tk('coefficients_invalid'))
        return
      }
      saved = {
        checker_version: STACK_VERSION,
        equation: quadraticEquation(...abc),
        stack_coefficients: abc,
        explanation: contents.explanation,
      }
    } else {
      const equation = contents.equation.trim()
      if (!equation) {
        setEquationError(tk('equation_invalid'))
        return
      }
      saved = { equation, explanation: contents.explanation }
    }
    setEquationError(null)
    const saveRes = await updateAssignmentTask(
      { contents: saved },
      assignmentTaskState.assignmentTask.assignment_task_uuid,
      assignmentUUID,
      access_token
    )
    if (saveRes.success) {
      assignmentTaskStateHook({ type: 'reload' })
      toast.success(t('dashboard.assignments.editor.toasts.task_updated'))
    } else if (saveRes.status === 400 && typeof saveRes.data?.detail === 'string') {
      setEquationError(`${tk('server_invalid')}: ${saveRes.data.detail}`)
    } else {
      toast.error(t('dashboard.assignments.editor.toasts.task_update_error'))
    }
  }

  // --- SAVE PROGRESS (student): persist the steps only, never a grade ---
  async function submitFC(opts?: { silent?: boolean }) {
    if (!assignmentTaskUUID) return true
    const res = await handleAssignmentTaskSubmission(
      {
        assignment_task_submission_uuid: userSubmissions?.assignment_task_submission_uuid || null,
        task_submission: { steps },
        grade: 0,
        task_submission_grade_feedback: '',
      },
      assignmentTaskUUID,
      assignmentUUID,
      access_token
    )
    if (res.success) {
      setUserSubmissions(res.data)
      setInitialSteps(steps)
      queryClient.setQueryData(
        queryKeys.assignments.taskSubmission(assignmentUUID),
        (prev: any) => (prev ? { ...prev, [assignmentTaskUUID]: res.data } : prev)
      )
      if (!opts?.silent) toast.success(t('dashboard.assignments.editor.toasts.task_saved'))
      return true
    }
    if (!opts?.silent) toast.error(t('dashboard.assignments.editor.toasts.task_save_error'))
    return false
  }

  const lastCheck = checks[steps.length - 1]
  const completed = steps.length > 0 && lastCheck?.status === 'valid' && lastCheck.terminal
  // A step can only follow a valid one; a wrong step is fixed by taking it back.
  const canAddStep = !answersLocked && !completed && serviceError !== 'restore'
    && (steps.length === 0 || lastCheck?.status === 'valid')

  async function addStep() {
    const current = draft.trim()
    if (!current || checking || !canAddStep) return
    interactedRef.current = true
    setChecking(true)
    const result = await check(previousFor(contents, steps), current)
    setChecking(false)
    // A checker outage is not the learner's mistake: keep the draft to retry.
    if (!result || result.status === 'check_failed') {
      setServiceError('step')
      return
    }
    setServiceError(null)
    setSteps((prev) => [...prev, current])
    setChecks((prev) => [...prev.slice(0, steps.length), result])
    setDraft('')
  }

  function takeBackLastStep() {
    if (!steps.length || answersLocked || checking || serviceError === 'restore') return
    interactedRef.current = true
    setDraft(steps[steps.length - 1])
    setSteps((prev) => prev.slice(0, -1))
    setChecks((prev) => prev.slice(0, steps.length - 1))
  }

  async function gradeCustomFC(grade: number, feedback?: string) {
    if (!userSubmissions) return
    await applyManualGrade({
      grade,
      feedback,
      maxPoints: taskOutsideProvider?.max_grade_value || assignmentTaskState?.assignmentTask?.max_grade_value || 100,
      assignmentTaskUUID,
      assignmentUUID,
      accessToken: access_token,
      username: session?.data?.user?.username,
      assignmentTaskSubmissionUUID: userSubmissions.assignment_task_submission_uuid,
      taskSubmissionPayload: userSubmissions.task_submission,
      onSuccess: () => { setReloadKey((k) => k + 1); onGraded?.() },
    })
  }

  const stepList = (
    <ol className="flex flex-col gap-1.5">
      <li className="font-mono text-base text-neutral-900 px-3 py-2 rounded-lg bg-neutral-50">{contents.equation}</li>
      {steps.map((step, i) => {
        const c = checks[i]
        const style = c ? STATUS_STYLE[c.status] : null
        return (
          <li key={i} className="flex flex-col gap-0.5 px-3 py-2 rounded-lg border border-neutral-100">
            <div className="flex items-center gap-2">
              <span className="text-neutral-400 font-mono">⇔</span>
              <span className="font-mono text-base text-neutral-900 flex-1">{step}</span>
              {style && <style.Icon size={18} weight="fill" className={style.className} />}
            </div>
            {c && c.status !== 'valid' && (
              <p className={`text-xs ${style?.className}`}>
                {c.feedback}
                {c.example ? ` ${tk('example')}: ${c.example}` : ''}
              </p>
            )}
            {c?.status === 'valid' && !c.terminal && i === steps.length - 1 && (
              <p className="text-xs text-emerald-700">{tk('valid_partial')}</p>
            )}
          </li>
        )
      })}
    </ol>
  )

  return (
    <AssignmentBoxUI
      type="steps"
      view={view}
      saveFC={saveFC}
      submitFC={submitFC}
      taskUUID={assignmentTaskUUID}
      gradeCustomFC={gradeCustomFC}
      currentPoints={userSubmissionObject?.grade}
      currentFeedback={userSubmissionObject?.task_submission_grade_feedback}
      maxPoints={taskOutsideProvider?.max_grade_value || assignmentTaskState?.assignmentTask?.max_grade_value}
      dirtyValue={JSON.stringify(steps)}
      savedValue={JSON.stringify(initialSteps)}
    >
      <div className="flex flex-col space-y-4">
        {view === 'teacher' && (
          <>
            <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">{tk('degree_label')}</label>
              <SegmentedControl<Degree>
                value={degree}
                onChange={(d) => { setEquationError(null); setDegree(d) }}
                ariaLabel={tk('degree_label')}
                options={[
                  { key: 'linear', label: tk('degree_linear') },
                  { key: 'quadratic', label: tk('degree_quadratic') },
                ]}
              />
            </div>
            {degree === 'linear' ? (
              <div className="flex flex-col space-y-1">
                <label className="text-xs font-semibold text-slate-500">{tk('equation_label')}</label>
                <input
                  value={contents.equation}
                  onChange={(e) => { setEquationError(null); setContents((prev) => ({ ...prev, equation: e.target.value })) }}
                  placeholder="2(x + 3) = 10"
                  className="px-3 py-1.5 text-sm font-mono border border-gray-200 rounded-md bg-white"
                />
                {equationError
                  ? <p className="text-[11px] text-rose-600">{equationError}</p>
                  : <p className="text-[10px] text-slate-400">{tk('equation_hint')}</p>}
              </div>
            ) : (
              <div className="flex flex-col space-y-1">
                <label className="text-xs font-semibold text-slate-500">{tk('coefficients_label')}</label>
                <div className="flex flex-wrap gap-2">
                  {(['a', 'b', 'c'] as const).map((name, i) => (
                    <label key={name} className="flex items-center gap-1.5 text-sm font-mono text-slate-600">
                      {name} =
                      <input
                        type="number"
                        step={1}
                        min={-MAX_STACK_COEFFICIENT}
                        max={MAX_STACK_COEFFICIENT}
                        value={coefficients[i]}
                        onChange={(e) => {
                          setEquationError(null)
                          setCoefficients((prev) => prev.map((v, j) => (j === i ? e.target.value : v)))
                        }}
                        placeholder={['1', '-5', '6'][i]}
                        className="w-24 px-3 py-1.5 text-sm font-mono border border-gray-200 rounded-md bg-white"
                      />
                    </label>
                  ))}
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-xs font-semibold text-slate-500">{tk('preview_label')}:</span>
                  <span className="font-mono text-neutral-900">
                    {(() => {
                      const abc = parseCoefficients(coefficients)
                      return abc ? quadraticEquation(...abc) : '—'
                    })()}
                  </span>
                </div>
                {equationError
                  ? <p className="text-[11px] text-rose-600">{equationError}</p>
                  : <p className="text-[10px] text-slate-400">{tk('coefficients_hint')}</p>}
              </div>
            )}
            <div className="flex flex-col space-y-1">
              <label className="text-xs font-semibold text-slate-500">{tk('explanation_label')}</label>
              <textarea
                value={contents.explanation ?? ''}
                onChange={(e) => setContents((prev) => ({ ...prev, explanation: e.target.value }))}
                placeholder={tk('explanation_placeholder')}
                rows={3}
                className="px-3 py-2 text-sm border border-gray-200 rounded-md bg-white resize-y"
              />
            </div>
          </>
        )}

        {view === 'student' && (
          <>
            <p className="text-sm text-neutral-600">{tk(isStack ? 'instructions_stack' : 'instructions')}</p>
            {stepList}
            {completed && (
              <div className="flex items-center gap-1.5 text-sm font-semibold text-emerald-700">
                <CheckCircle size={16} weight="fill" /> {tk('completed')}
              </div>
            )}
            {!answersLocked && (
              <div className="flex flex-col gap-2">
                {canAddStep && (
                  <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addStep() } }}
                    readOnly={checking}
                    placeholder={tk(isStack ? 'step_placeholder_stack' : 'step_placeholder')}
                    className={`${answerInputClass} font-mono`}
                  />
                )}
                {serviceError && (
                  <div role="alert" className="flex items-center gap-2 text-xs text-neutral-600">
                    <WarningCircle size={14} weight="fill" className="text-neutral-500" />
                    {tk(serviceError === 'restore' ? 'restore_failed' : 'check_failed')}
                    <button
                      type="button"
                      onClick={() => (serviceError === 'restore' ? checkAll(contents, steps) : addStep())}
                      disabled={checking}
                      className="inline-flex items-center gap-1 font-semibold text-neutral-700 hover:text-neutral-900"
                    >
                      <ArrowClockwise size={13} /> {tk('retry')}
                    </button>
                  </div>
                )}
                {!canAddStep && !completed && !checking && !serviceError && <p className="text-xs text-neutral-500">{tk('fix_last_hint')}</p>}
                {steps.length > 0 && (
                  <button
                    type="button"
                    onClick={takeBackLastStep}
                    className="self-start inline-flex items-center gap-1 text-xs font-semibold text-neutral-600 hover:text-neutral-900"
                  >
                    <ArrowCounterClockwise size={13} /> {tk('take_back')}
                  </button>
                )}
              </div>
            )}
            {showCorrectAnswers && contents.explanation && (
              <div className={answerKeyPanelClass}>
                <div className="text-xs font-semibold text-emerald-700">{tk('worked_solution_label')}</div>
                <p className="text-sm text-emerald-800 whitespace-pre-wrap">{contents.explanation}</p>
              </div>
            )}
          </>
        )}

        {view === 'grading' && (
          <>
            {steps.length ? stepList : <p className="text-sm text-gray-400 italic">{tk('no_steps')}</p>}
            <div
              className={`flex items-center space-x-2 p-2.5 rounded-md text-xs font-semibold ${
                userSubmissionObject?.grade > 0
                  ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                  : 'bg-rose-50 text-rose-700 border border-rose-200'
              }`}
            >
              {userSubmissionObject?.grade > 0 ? <CheckCircle size={14} /> : <XCircle size={14} />}
              <span>{userSubmissionObject?.grade > 0 ? tk('correct') : tk('incorrect')}</span>
            </div>
          </>
        )}
      </div>
    </AssignmentBoxUI>
  )
}

export default TaskMathStepsObject
