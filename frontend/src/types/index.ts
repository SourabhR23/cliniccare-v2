export interface User {
  id: string
  email: string
  name: string
  role: 'doctor' | 'receptionist' | 'admin'
  specialization?: string
  is_active: boolean
}

export interface AuthState {
  user: User | null
  token: string | null
  login: (token: string, user: User) => void
  logout: () => void
}

export interface PatientListItem {
  id: string
  name: string
  age: number
  sex: string
  blood_group: string
  phone: string
  known_allergies: string[]
  chronic_conditions: string[]
  total_visits: number
  last_visit_date: string | null
  pending_followup_date: string | null
}

export interface PatientResponse extends PatientListItem {
  email: string | null
  address: string | null
  assigned_doctor_id: string
  registered_date: string
}

export interface Medication {
  name: string
  dose: string
  frequency: string
  duration: string
  notes?: string
}

export interface VisitDocument {
  id: string
  patient_id: string
  patient_name: string
  doctor_id: string
  doctor_name: string
  visit_date: string
  weight_kg?: number
  bp?: string
  visit_type: string
  chief_complaint: string
  symptoms: string
  diagnosis: string
  medications: Medication[]
  notes?: string
  followup_required: boolean
  followup_date?: string
  embedding_status: string
  created_at: string
}

export interface RAGSource {
  visit_id: string
  visit_date: string
  visit_type: string
  diagnosis: string
  doctor_name: string
  rerank_score: number
}

export interface RAGQueryResponse {
  answer: string
  sources: RAGSource[]
  cached: boolean
  retrieval_count: number
}

export interface PrevisitBrief {
  brief: string
  sources: RAGSource[]
  cached: boolean
}

export interface EmbedQueueStatus {
  pending: number
  embedded: number
  failed: number
  chroma_total: number
}

export interface EmbedBatchResult {
  total: number
  embedded: number
  failed: number
  duration_seconds: number
}

export interface ChatMessage {
  role: string
  content: string
}

export interface RAGChatHistoryItem {
  role: 'user' | 'assistant'
  content: string
}

export interface AgentChatResponse {
  thread_id: string
  response: string
  current_agent: string
  patient_id?: string
  session_done?: boolean
  // RAG-specific — present when current_agent is RAGAgent
  sources?: RAGSource[]
  cached?: boolean
  retrieval_count?: number
}

export interface AgentUISlotPicker {
  type: 'slot_picker'
  patient_name: string
  patient_id: string
  doctor_name: string
  doctor_id: string
  appointment_date: string
  slots: string[]
  reason: string
  registration_success?: boolean
}

export interface AgentUIBookingConfirm {
  type: 'booking_confirm'
  appointment_id: string
  patient_name: string
  doctor_name: string
  appointment_date: string
  appointment_slot: string
  reason: string
  patient_email: string
  email_sent: boolean
  email_pending?: boolean
  follow_up?: string
}

export interface AgentUIRegisterPrompt {
  type: 'register_prompt'
  patient_name: string
}

export interface AgentUIRegistrationForm {
  type: 'registration_form'
  patient_name: string
  message?: string
  doctors: Array<{ id: string; name: string; specialization?: string | null }>
}

export interface AgentUIDoctorPicker {
  type: 'doctor_picker'
  patient_name: string
  patient_id: string
  appointment_date?: string
  doctors: Array<{ id: string; name: string; specialization?: string | null }>
}

export type AgentUIData = AgentUISlotPicker | AgentUIBookingConfirm | AgentUIRegisterPrompt | AgentUIRegistrationForm | AgentUIDoctorPicker

export interface AgentThread {
  thread_id: string
  messages: ChatMessage[]
  current_agent: string
}

export interface Doctor {
  id: string
  name: string
  specialization: string
}

export interface HealthStatus {
  status: string
  [key: string]: unknown
}

export type UserRole = 'doctor' | 'receptionist' | 'admin'

export interface AgentOverallStats {
  total_calls: number
  avg_latency_ms: number
  max_latency_ms: number
  total_input_tokens: number
  total_output_tokens: number
  fallback_count: number
  error_count: number
  fallback_rate: number
  error_rate: number
}

export interface AgentRowStats {
  agent: string
  call_count: number
  avg_latency_ms: number
  max_latency_ms: number
  total_input_tokens: number
  total_output_tokens: number
  error_count: number
  fallback_count: number
}

export interface AgentWarning {
  level: 'warning' | 'error'
  message: string
}

export interface AgentStatsResponse {
  days: number
  since: string
  overall: AgentOverallStats
  by_agent: AgentRowStats[]
  warnings: AgentWarning[]
}

export interface StaffUser {
  id: string
  name: string
  email: string
  role: 'doctor' | 'receptionist' | 'admin'
  specialization?: string
  is_active: boolean
}

export interface AgentLogEntry {
  thread_id: string
  timestamp: string
  staff_id: string
  staff_role: string
  agent: string
  latency_ms: number
  input_tokens: number
  output_tokens: number
  supervisor_confidence: number
  tool_calls_made: number
  fallback: boolean
  error: string | null
  cache_hit: boolean | null
  smtp_sent: boolean | null
}
