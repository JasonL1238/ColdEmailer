/* Shared test doubles for the three modules every page suite has to stub:
   `src/api`, `react-hot-toast`, and `src/App`'s `useApp` context.

   Pull them in through an ASYNC `vi.mock` factory:

     vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

   never through a plain top-level import. `vi.mock` factories run before the
   test file's own body, so a name imported at the top of the file is not
   reliably bound yet — the same hoisting trap that made emails-cadence.test.jsx
   reach for `vi.hoisted`. The dynamic import inside the factory is ordering-proof.

   For the same reason, row data stays in the suite that asserts on it and is
   passed as a lazy `vi.fn(() => Promise.resolve({ data: rows }))`: the array is
   dereferenced when the stub is CALLED, by which time the module body has run. */
import { vi } from 'vitest'

/** A stub resolving to an axios-shaped `{ data }`. */
export const json = (data) => vi.fn(() => Promise.resolve({ data }))

/** `react-hot-toast`, callable and carrying every method a page reaches for.
 *  Suites that assert on toast bind their own spies instead of using this. */
export const toastMock = () => ({
  default: Object.assign(vi.fn(), {
    success: vi.fn(), error: vi.fn(), loading: vi.fn(), dismiss: vi.fn(),
  }),
})

/** `src/App`. A fresh context object per `useApp()` call, like the real one. */
export const appMock = (ctx) => ({
  useApp: () => ({ navigate: vi.fn(), settings: {}, ...ctx }),
})

/** The plain error-message reader. Suites whose page surfaces a backend
 *  `detail` string keep their own richer version. */
export const errMessage = (e, fallback) => fallback || 'err'

/** The `src/api` surface the Emails page touches on mount. A suite overrides
 *  the handful of stubs it asserts on and inherits the rest. */
export const emailsPageApi = ({ emailsAPI, resumesAPI, sendWindowAPI, jobsAPI } = {}) => ({
  errMessage,
  emailsAPI: {
    list: json([]),
    followUps: json([]),
    update: json({}),
    bulkStatus: json({ updated: 0 }),
    generateFollowUp: json({}),
    send: json({ id: 'job1', status: 'running' }),
    ...emailsAPI,
  },
  resumesAPI: { list: json([]), ...resumesAPI },
  sendWindowAPI: { get: json({ enabled: false }), ...sendWindowAPI },
  jobsAPI: { get: json({ id: 'job1', status: 'running' }), ...jobsAPI },
})

/** The `src/api` surface ComposeModal touches: generate, cancel, resumes and
 *  the job it polls while drafting. */
export const composeModalApi = ({ emailsAPI, resumesAPI, jobsAPI } = {}) => ({
  errMessage,
  emailsAPI: {
    generate: json({ id: 'job1', status: 'running' }),
    cancelGeneration: vi.fn(() => Promise.resolve({})),
    ...emailsAPI,
  },
  resumesAPI: {
    list: json([{ id: 'r1', label: 'ZZTEST resume', is_default: true }]),
    ...resumesAPI,
  },
  jobsAPI: { get: json({ id: 'job1', status: 'running' }), ...jobsAPI },
})
