import axios from 'axios'
import { useAuthStore } from '@/store/auth'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export const api = axios.create({
  baseURL: `${BASE_URL}/api`,
})

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (err) => {
    if (err.response?.status === 401) {
      useAuthStore.getState().logout()
      if (typeof window !== 'undefined') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(err)
  }
)

// Auth
export const loginApi = (email: string, password: string) => {
  const params = new URLSearchParams({ username: email, password })
  return api.post('/auth/login', params, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })
}

// Patients
export const listPatients = (skip = 0, limit = 20) =>
  api.get('/patients/', { params: { skip, limit } })

export const searchPatients = (q: string) =>
  api.get('/patients/search', { params: { q } })

export const getPatient = (id: string) => api.get(`/patients/${id}`)

export const createPatient = (data: Record<string, unknown>) =>
  api.post('/patients/', data)

export const updatePatient = (id: string, data: Record<string, unknown>) =>
  api.patch(`/patients/${id}`, data)

export const deletePatient = (id: string) =>
  api.delete(`/patients/${id}`)

export const updateVisit = (patientId: string, visitId: string, data: Record<string, unknown>) =>
  api.patch(`/patients/${patientId}/visits/${visitId}`, data)

export const deleteVisit = (patientId: string, visitId: string) =>
  api.delete(`/patients/${patientId}/visits/${visitId}`)

export const getPatientVisits = (patientId: string) =>
  api.get(`/patients/${patientId}/visits`)

export const addVisit = (patientId: string, data: Record<string, unknown>) =>
  api.post(`/patients/${patientId}/visit`, data)

export const listDoctors = () => api.get('/patients/doctors/list')

// RAG
export const ragQuery = (query: string, patient_id?: string) =>
  api.post('/rag/query', { query, patient_id })

export const ragChatQuery = (
  message: string,
  history: { role: string; content: string }[],
  patient_id?: string,
) => api.post('/rag/chat', { message, patient_id, history })

export const previsitBrief = (patientId: string) =>
  api.get(`/rag/previsit-brief/${patientId}`)

// Admin
export const embedBatch = (pipelineKey?: string) =>
  api.post('/admin/embed-batch', undefined, {
    headers: pipelineKey ? { 'X-Pipeline-Key': pipelineKey } : {},
  })

export const retryFailed = () => api.post('/admin/retry-failed')

export const getQueue = () => api.get('/admin/queue')

export const syncCheck = () => api.get('/admin/sync-check')

export const syncFix = () => api.post('/admin/sync-fix')

export const getAgentStats = (days = 7) =>
  api.get('/admin/agent-stats', { params: { days } })

export const getAgentLogs = (params?: { limit?: number; agent?: string; role?: string }) =>
  api.get('/admin/agent-logs', { params })

export const listAdminUsers = () => api.get('/admin/users')

export const getAuditLogs = (params?: { limit?: number; action?: string; resource_type?: string }) =>
  api.get('/admin/audit-logs', { params })

export const getAnalytics = (months = 6) =>
  api.get('/admin/analytics', { params: { months } })

// PDF Export (doctor-only — browser download)
export const downloadPatientPdf = (patientId: string) =>
  api.get(`/pdf/patient/${patientId}`, { responseType: 'blob' })

export const downloadVisitPdf = (visitId: string) =>
  api.get(`/pdf/visit/${visitId}`, { responseType: 'blob' })

export const emailPatientPdf = (patientId: string) =>
  api.post(`/pdf/patient/${patientId}/email`)

export const emailVisitPdf = (visitId: string) =>
  api.post(`/pdf/visit/${visitId}/email`)

// Agents
export const agentChat = (
  message: string,
  thread_id?: string,
  patient_id?: string
) => api.post('/agents/chat', { message, thread_id, patient_id })

export const getThread = (threadId: string) =>
  api.get(`/agents/thread/${threadId}`)

// Appointments / Calendar
export const listAppointments = (month?: string) =>
  api.get('/appointments/', { params: month ? { month } : {} })

export const cancelAppointment = (id: string) =>
  api.patch(`/appointments/${id}/cancel`)

export const deleteAppointment = (id: string) =>
  api.delete(`/appointments/${id}`)

export const notifyAppointment = (id: string) =>
  api.post(`/appointments/${id}/notify`)

// Health
export const getHealth = () =>
  axios.get(`${BASE_URL}/health`)

// Patient chatbot (public — no auth token needed)
export const patientChatApi = (message: string, session_id?: string | null) =>
  axios.post(`${BASE_URL}/api/patient/chat`, { message, session_id: session_id ?? undefined })

export const getPatientDoctors = () =>
  axios.get(`${BASE_URL}/api/patient/doctors`)

export const getPatientSlots = (doctor_id: string, date: string) =>
  axios.get(`${BASE_URL}/api/patient/slots`, { params: { doctor_id, date } })
