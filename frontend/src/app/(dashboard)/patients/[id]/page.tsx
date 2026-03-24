'use client'

import { useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { getPatient, getPatientVisits, addVisit, updatePatient, deletePatient, updateVisit, deleteVisit, ragQuery, previsitBrief, downloadPatientPdf, downloadVisitPdf, emailPatientPdf, emailVisitPdf } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { formatDate, formatScore, cn } from '@/lib/utils'
import { PatientResponse, VisitDocument, RAGQueryResponse, RAGSource, PrevisitBrief } from '@/types'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Input, Textarea, Select } from '@/components/ui/Input'
import { Spinner } from '@/components/ui/Spinner'
import { Modal } from '@/components/ui/Modal'

// Visit form schema
const medicationSchema = z.object({
  name: z.string().min(1, 'Drug name is required'),
  dose: z.string().min(1, 'Dose is required (e.g. 500mg)'),
  frequency: z.string().min(1, 'Frequency is required (e.g. Twice daily)'),
  duration: z.string().min(1, 'Duration is required (e.g. 7 days)'),
  notes: z.string().optional(),
})

const visitSchema = z.object({
  visit_type: z.string().min(1, 'Visit type is required'),
  chief_complaint: z.string().min(2, 'Chief complaint must be at least 2 characters'),
  symptoms: z.string().min(2, 'Symptoms must be at least 2 characters'),
  diagnosis: z.string().min(2, 'Diagnosis must be at least 2 characters'),
  medications: z.array(medicationSchema),
  weight_kg: z.preprocess(
    (v) => (v === '' || v === null || v === undefined ? undefined : Math.round(Number(v))),
    z.number({ required_error: 'Weight is required', invalid_type_error: 'Weight must be a number' })
      .int('Weight must be a whole number')
      .min(1, 'Weight must be at least 1 kg')
  ),
  bp: z.preprocess(
    (v) => (v === '' || v === null ? undefined : v),
    z.string().regex(/^\d{2,3}\/\d{2,3}$/, 'Blood pressure must be in format 120/80').optional()
  ),
  notes: z.string().optional(),
  followup_required: z.boolean(),
  followup_date: z.string().optional(),
})

type VisitForm = z.infer<typeof visitSchema>

const visitTypeOptions = [
  { value: 'New complaint', label: 'New Complaint' },
  { value: 'Follow-up', label: 'Follow-up' },
  { value: 'Emergency', label: 'Emergency' },
  { value: 'Routine checkup', label: 'Routine Checkup' },
]

// ---- EditPatientModal ----
const editPatientSchema = z.object({
  name: z.string().min(2, 'Name must be at least 2 characters'),
  date_of_birth: z.string().min(1, 'Date of birth is required'),
  sex: z.enum(['M', 'F', 'O'], { required_error: 'Sex is required' }),
  blood_group: z.enum(['A+', 'A-', 'B+', 'B-', 'O+', 'O-', 'AB+', 'AB-', 'Unknown']).optional(),
  phone: z.string().min(1, 'Phone is required'),
  email: z.string().min(1, 'Email is required').email('Valid email is required'),
  address: z.preprocess((v) => (v === '' ? undefined : v), z.string().optional()),
  emergency_contact: z.preprocess((v) => (v === '' ? undefined : v), z.string().optional()),
  known_allergies: z.string().optional(),
  chronic_conditions: z.string().optional(),
})
type EditPatientForm = z.infer<typeof editPatientSchema>

function EditPatientModal({
  patient,
  open,
  onClose,
}: {
  patient: PatientResponse
  open: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const router = useRouter()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () => deletePatient(patient.id),
    onSuccess: () => {
      toast.success('Patient permanently deleted.')
      queryClient.invalidateQueries({ queryKey: ['patients'] })
      onClose()
      router.push('/patients')
    },
    onError: () => toast.error('Failed to delete patient'),
  })

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<EditPatientForm>({
    resolver: zodResolver(editPatientSchema),
    defaultValues: {
      name: patient.name,
      date_of_birth: patient.registered_date ? (() => {
        // date_of_birth not exposed on PatientResponse — leave blank for user to fill if needed
        return ''
      })() : '',
      sex: (patient.sex as 'M' | 'F' | 'O') || 'M',
      blood_group: (patient.blood_group as EditPatientForm['blood_group']) || 'Unknown',
      phone: patient.phone,
      email: patient.email || '',
      address: patient.address || '',
      emergency_contact: '',
      known_allergies: patient.known_allergies?.join(', ') || '',
      chronic_conditions: patient.chronic_conditions?.join(', ') || '',
    },
  })

  const mutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => updatePatient(patient.id, data),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['patient', patient.id] })
      onClose()
      const reEmbed = res.data?.re_embed_required
      if (reEmbed) {
        toast.warning(
          'Patient updated. Existing embeddings were cleared — admin must re-run the embedding pipeline.',
          { duration: 6000 }
        )
      } else {
        toast.success('Patient updated successfully')
      }
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: unknown } } }
      const detail = error?.response?.data?.detail
      const msg = typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
        ? (detail as { msg?: string }[]).map((e) => e.msg || JSON.stringify(e)).join('; ')
        : 'Failed to update patient'
      toast.error(msg)
    },
  })

  const onSubmit = (data: EditPatientForm) => {
    const payload: Record<string, unknown> = {
      name: data.name,
      sex: data.sex,
      blood_group: data.blood_group,
      phone: data.phone,
      email: data.email || undefined,
      address: data.address || undefined,
      emergency_contact: data.emergency_contact || undefined,
      known_allergies: data.known_allergies
        ? data.known_allergies.split(',').map((s) => s.trim()).filter(Boolean)
        : undefined,
      chronic_conditions: data.chronic_conditions
        ? data.chronic_conditions.split(',').map((s) => s.trim()).filter(Boolean)
        : undefined,
    }
    if (data.date_of_birth) payload.date_of_birth = data.date_of_birth
    mutation.mutate(payload)
  }

  return (
    <Modal open={open} onClose={onClose} title="Edit Patient" size="xl">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Full Name"
            placeholder="Patient full name"
            error={errors.name?.message}
            {...register('name')}
          />
          <Input
            label="Date of Birth"
            type="date"
            error={errors.date_of_birth?.message}
            {...register('date_of_birth')}
          />
          <Select
            label="Sex"
            options={[
              { value: 'M', label: 'Male' },
              { value: 'F', label: 'Female' },
              { value: 'O', label: 'Other' },
            ]}
            error={errors.sex?.message}
            {...register('sex')}
          />
          <Select
            label="Blood Group"
            options={[
              'A+', 'A-', 'B+', 'B-', 'O+', 'O-', 'AB+', 'AB-', 'Unknown',
            ].map((v) => ({ value: v, label: v }))}
            error={errors.blood_group?.message}
            {...register('blood_group')}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Phone"
            placeholder="+91XXXXXXXXXX"
            error={errors.phone?.message}
            {...register('phone')}
          />
          <Input
            label="Email"
            type="email"
            placeholder="patient@email.com"
            error={errors.email?.message}
            {...register('email')}
          />
        </div>

        <Input
          label="Address (optional)"
          placeholder="Street, City"
          error={errors.address?.message}
          {...register('address')}
        />

        <Input
          label="Emergency Contact (optional)"
          placeholder="+91XXXXXXXXXX"
          error={errors.emergency_contact?.message}
          {...register('emergency_contact')}
        />

        <Input
          label="Known Allergies (comma-separated)"
          placeholder="e.g. Penicillin, Aspirin"
          error={errors.known_allergies?.message}
          {...register('known_allergies')}
        />

        <Input
          label="Chronic Conditions (comma-separated)"
          placeholder="e.g. Diabetes, Hypertension"
          error={errors.chronic_conditions?.message}
          {...register('chronic_conditions')}
        />

        <div className="rounded-[10px] border border-amber-500/20 bg-amber-500/5 px-4 py-3">
          <p className="text-xs text-amber-400/80 font-sans">
            If this patient has embedded visit records, saving will clear those embeddings.
            Admin must re-run the embedding pipeline after editing.
          </p>
        </div>

        <div className="flex gap-3 pt-1">
          <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
            Cancel
          </Button>
          <Button type="submit" loading={mutation.isPending} className="flex-1">
            Save Changes
          </Button>
        </div>

        {/* Delete patient */}
        <div className="border-t border-[#c8dde6] pt-4 mt-2">
          {!confirmDelete ? (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="text-xs text-red-400/70 hover:text-red-400 font-sans transition-colors"
            >
              Delete this patient permanently
            </button>
          ) : (
            <div className="rounded-[10px] border border-red-500/30 bg-red-500/5 p-4 space-y-3">
              <p className="text-sm font-semibold text-red-400">Permanently delete patient?</p>
              <p className="text-xs text-[#5a8898] leading-relaxed">
                This will permanently delete <span className="text-[#052838] font-medium">{patient.name}</span>,
                all their visit records, and all ChromaDB embeddings.
                <span className="text-red-400 font-medium"> This cannot be undone.</span>
              </p>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setConfirmDelete(false)}
                  className="flex-1"
                >
                  Cancel
                </Button>
                <button
                  type="button"
                  onClick={() => deleteMutation.mutate()}
                  disabled={deleteMutation.isPending}
                  className="flex-1 px-3 py-2 text-xs font-medium bg-red-500/15 border border-red-500/30 text-red-400 hover:bg-red-500/25 rounded-[8px] transition-colors disabled:opacity-50"
                >
                  {deleteMutation.isPending ? 'Deleting...' : 'Yes, delete permanently'}
                </button>
              </div>
            </div>
          )}
        </div>
      </form>
    </Modal>
  )
}

// ---- Sub-components ----
function PatientHeader({ patient, onEdit, onExportPdf, onEmailPdf }: {
  patient: PatientResponse
  onEdit?: () => void
  onExportPdf?: () => void
  onEmailPdf?: () => void
}) {
  return (
    <Card className="p-5">
      <div className="flex flex-col sm:flex-row sm:items-start gap-4">
        <div className="w-14 h-14 rounded-2xl bg-sky/10 border border-sky/20 flex items-center justify-center flex-shrink-0">
          <span className="text-sky text-xl font-sans font-semibold">
            {patient.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)}
          </span>
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1.5">
            <h2 className="text-lg font-bold text-[#052838]">{patient.name}</h2>
            <Badge variant="default">{patient.blood_group}</Badge>
            {patient.sex && (
              <Badge variant="muted">{patient.sex === 'M' ? 'Male' : patient.sex === 'F' ? 'Female' : patient.sex}</Badge>
            )}
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-sans text-[#5a8898] mb-3">
            <span>{patient.age} years old</span>
            <span>{patient.phone}</span>
            {patient.email && <span>{patient.email}</span>}
            {patient.address && <span>{patient.address}</span>}
          </div>

          {/* Allergies */}
          {patient.known_allergies?.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {patient.known_allergies.map((a) => (
                <Badge key={a} variant="allergy">{a}</Badge>
              ))}
            </div>
          )}

          {/* Conditions */}
          {patient.chronic_conditions?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {patient.chronic_conditions.map((c) => (
                <Badge key={c} variant="condition">{c}</Badge>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <div className="text-right">
            <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider">Total Visits</p>
            <p className="font-sans text-2xl text-sky font-semibold">{patient.total_visits}</p>
          </div>
          <div className="text-right">
            <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider">Last Visit</p>
            <p className="font-sans text-xs text-[#5a8898]">{formatDate(patient.last_visit_date)}</p>
          </div>
          {patient.pending_followup_date && (
            <Badge variant="warning">Follow-up: {formatDate(patient.pending_followup_date)}</Badge>
          )}
          {onExportPdf && (
            <Button size="sm" variant="secondary" onClick={onExportPdf}>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Export PDF
            </Button>
          )}
          {onEmailPdf && (
            <Button size="sm" variant="secondary" onClick={onEmailPdf}>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
              </svg>
              Email PDF
            </Button>
          )}
          {onEdit && (
            <Button size="sm" variant="secondary" onClick={onEdit}>
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
              Edit
            </Button>
          )}
        </div>
      </div>
    </Card>
  )
}

// ---- EditVisitModal ----
function EditVisitModal({
  visit,
  patientId,
  open,
  onClose,
}: {
  visit: VisitDocument
  patientId: string
  open: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm({
    resolver: zodResolver(
      z.object({
        visit_type: z.string().min(1, 'Visit type is required'),
        chief_complaint: z.string().min(2, 'Chief complaint required'),
        symptoms: z.string().min(2, 'Symptoms required'),
        diagnosis: z.string().min(2, 'Diagnosis required'),
        bp: z.preprocess((v) => (v === '' ? undefined : v), z.string().regex(/^\d{2,3}\/\d{2,3}$/, 'Format: 120/80').optional()),
        weight_kg: z.preprocess(
          (v) => (v === '' || v === null || v === undefined ? undefined : Math.round(Number(v))),
          z.number({ required_error: 'Weight is required', invalid_type_error: 'Weight must be a number' })
            .int('Weight must be a whole number')
            .min(1, 'Weight must be at least 1 kg')
        ),
        notes: z.string().optional(),
        followup_required: z.boolean(),
        followup_date: z.string().optional(),
      })
    ),
    defaultValues: {
      visit_type: visit.visit_type,
      chief_complaint: visit.chief_complaint,
      symptoms: visit.symptoms,
      diagnosis: visit.diagnosis,
      bp: visit.bp || '',
      weight_kg: visit.weight_kg ?? undefined,
      notes: visit.notes || '',
      followup_required: visit.followup_required,
      followup_date: visit.followup_date ? String(visit.followup_date) : '',
    },
  })

  const mutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => updateVisit(patientId, visit.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['patient', patientId, 'visits'] })
      toast.success('Visit updated. Will be re-embedded on next pipeline run.')
      onClose()
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } } }
      toast.error(error?.response?.data?.detail || 'Failed to update visit')
    },
  })

  const onSubmit = (data: Record<string, unknown>) => {
    const payload = { ...data }
    if (!payload.bp) delete payload.bp
    if (!payload.notes) delete payload.notes
    mutation.mutate(payload)
  }

  return (
    <Modal open={open} onClose={onClose} title={`Edit Visit — ${formatDate(visit.visit_date)}`} size="xl">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <Select
            label="Visit Type"
            options={visitTypeOptions}
            error={(errors as Record<string, { message?: string } | undefined>).visit_type?.message}
            {...register('visit_type')}
          />
          <Input
            label="Blood Pressure (optional)"
            placeholder="120/80"
            error={(errors as Record<string, { message?: string }>).bp?.message}
            {...register('bp')}
          />
          <Input
            label="Weight (kg)"
            type="number"
            step="1"
            placeholder="65"
            error={(errors as Record<string, { message?: string }>).weight_kg?.message}
            {...register('weight_kg')}
          />
        </div>
        <Textarea
          label="Chief Complaint"
          rows={2}
          error={(errors as Record<string, { message?: string }>).chief_complaint?.message}
          {...register('chief_complaint')}
        />
        <Textarea
          label="Symptoms"
          rows={3}
          error={(errors as Record<string, { message?: string }>).symptoms?.message}
          {...register('symptoms')}
        />
        <Textarea
          label="Diagnosis"
          rows={2}
          error={(errors as Record<string, { message?: string }>).diagnosis?.message}
          {...register('diagnosis')}
        />
        <Textarea
          label="Clinical Notes (optional)"
          rows={2}
          {...register('notes')}
        />
        <div className="flex gap-3 pt-1">
          <Button type="button" variant="secondary" onClick={onClose} className="flex-1">Cancel</Button>
          <Button type="submit" loading={mutation.isPending} className="flex-1">Save Visit</Button>
        </div>
      </form>
    </Modal>
  )
}

function VisitCard({
  visit,
  patientId,
  canEdit,
  canExport,
  patientEmail,
}: {
  visit: VisitDocument
  patientId: string
  canEdit: boolean
  canExport?: boolean
  patientEmail?: string | null
}) {
  const [expanded, setExpanded] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const queryClient = useQueryClient()

  const embeddingColor = visit.embedding_status === 'embedded'
    ? 'text-teal'
    : visit.embedding_status === 'failed'
    ? 'text-red-400'
    : 'text-[#8aaab8]'

  const deleteMutation = useMutation({
    mutationFn: () => deleteVisit(patientId, visit.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['patient', patientId, 'visits'] })
      queryClient.invalidateQueries({ queryKey: ['patient', patientId] })
      toast.success('Visit deleted.')
      setConfirmDelete(false)
    },
    onError: () => toast.error('Failed to delete visit'),
  })

  return (
    <div className="border-l-2 border-[#c8dde6] pl-4 pb-6 relative">
      <div className="absolute -left-[5px] top-0 w-2.5 h-2.5 rounded-full bg-sky/40 border-2 border-sky/20" />

      <Card className="overflow-hidden">
        <div
          onClick={() => setExpanded((v) => !v)}
          className="px-4 py-3.5 flex items-center justify-between gap-3 cursor-pointer hover:bg-[#e8f2f6] transition-colors"
        >
          <div className="flex flex-wrap items-center gap-2.5 min-w-0">
            <span className="font-sans text-xs text-sky">{formatDate(visit.visit_date)}</span>
            <Badge variant="muted">{visit.visit_type}</Badge>
            <span className="text-sm font-medium text-[#052838] truncate">{visit.chief_complaint}</span>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className={cn('text-[9px] font-sans uppercase', embeddingColor)}>
              {visit.embedding_status}
            </span>
            <svg
              className={cn('w-4 h-4 text-[#8aaab8] transition-transform', expanded && 'rotate-180')}
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {expanded && (
          <div className="px-4 pb-4 pt-1 border-t border-[#c8dde6] space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {visit.weight_kg && (
                <div>
                  <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-0.5">Weight</p>
                  <p className="text-sm font-sans text-[#052838]">{visit.weight_kg}kg</p>
                </div>
              )}
              {visit.bp && (
                <div>
                  <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-0.5">BP</p>
                  <p className="text-sm font-sans text-[#052838]">{visit.bp}</p>
                </div>
              )}
            </div>
            <div>
              <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-1">Symptoms</p>
              <p className="text-sm text-[#5a8898] leading-relaxed">{visit.symptoms}</p>
            </div>
            <div>
              <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-1">Diagnosis</p>
              <p className="text-sm text-[#052838] font-medium leading-relaxed">{visit.diagnosis}</p>
            </div>
            {visit.medications?.length > 0 && (
              <div>
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Medications</p>
                <div className="space-y-1.5">
                  {visit.medications.map((med, i) => (
                    <div key={i} className="flex flex-wrap gap-x-3 gap-y-1 bg-[#e8f2f6] rounded-[8px] px-3 py-2">
                      <span className="text-sm font-medium text-sky">{med.name}</span>
                      <span className="text-xs font-sans text-[#5a8898]">{med.dose}</span>
                      <span className="text-xs font-sans text-[#5a8898]">{med.frequency}</span>
                      <span className="text-xs font-sans text-[#5a8898]">{med.duration}</span>
                      {med.notes && <span className="text-xs text-[#8aaab8]">{med.notes}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {visit.notes && (
              <div>
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-1">Notes</p>
                <p className="text-sm text-[#5a8898] leading-relaxed">{visit.notes}</p>
              </div>
            )}
            {visit.followup_required && (
              <div className="flex items-center gap-2">
                <Badge variant="warning">Follow-up Required</Badge>
                {visit.followup_date && (
                  <span className="text-xs font-sans text-yellow-400">{formatDate(visit.followup_date)}</span>
                )}
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <span className="text-[10px] font-sans text-[#8aaab8]">{visit.doctor_name}</span>

              <div className="flex items-center gap-2">
                {canExport && (
                  <>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation()
                        try {
                          const res = await downloadVisitPdf(visit.id)
                          triggerBlobDownload(res.data as Blob, `visit_${visit.id}.pdf`)
                        } catch { toast.error('PDF export failed') }
                      }}
                      className="text-[11px] font-sans text-[#8aaab8] hover:text-sky transition-colors"
                    >
                      PDF
                    </button>
                    {patientEmail && (
                      <>
                        <span className="text-[#8aaab8]">·</span>
                        <button
                          onClick={async (e) => {
                            e.stopPropagation()
                            try {
                              const res = await emailVisitPdf(visit.id)
                              toast.success(`Visit PDF sent to ${res.data.recipient}`)
                            } catch (err: unknown) {
                              const error = err as { response?: { data?: { detail?: string } } }
                              toast.error(error?.response?.data?.detail || 'Failed to send email')
                            }
                          }}
                          className="text-[11px] font-sans text-[#8aaab8] hover:text-teal transition-colors"
                        >
                          Email
                        </button>
                      </>
                    )}
                  </>
                )}
                {canEdit && !confirmDelete && (
                  <>
                    {canExport && <span className="text-[#8aaab8]">·</span>}
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowEditModal(true) }}
                      className="text-[11px] font-sans text-sky/60 hover:text-sky transition-colors"
                    >
                      Edit
                    </button>
                    <span className="text-[#8aaab8]">·</span>
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDelete(true) }}
                      className="text-[11px] font-sans text-red-400/50 hover:text-red-400 transition-colors"
                    >
                      Delete
                    </button>
                  </>
                )}
              </div>
            </div>

            {confirmDelete && (
              <div className="rounded-[8px] border border-red-500/25 bg-red-500/5 p-3 space-y-2">
                <p className="text-xs text-red-400">Delete this visit? This will also remove it from ChromaDB and cannot be undone.</p>
                <div className="flex gap-2">
                  <Button type="button" variant="secondary" size="sm" onClick={() => setConfirmDelete(false)} className="flex-1">
                    Cancel
                  </Button>
                  <button
                    onClick={() => deleteMutation.mutate()}
                    disabled={deleteMutation.isPending}
                    className="flex-1 px-3 py-1.5 text-xs font-medium bg-red-500/15 border border-red-500/30 text-red-400 hover:bg-red-500/25 rounded-[8px] transition-colors disabled:opacity-50"
                  >
                    {deleteMutation.isPending ? 'Deleting...' : 'Yes, delete'}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </Card>

      {showEditModal && (
        <EditVisitModal
          visit={visit}
          patientId={patientId}
          open={showEditModal}
          onClose={() => setShowEditModal(false)}
        />
      )}
    </div>
  )
}

function RAGPanel({ patientId }: { patientId: string }) {
  const [query, setQuery] = useState('')
  const [ragResult, setRagResult] = useState<RAGQueryResponse | null>(null)
  const [briefResult, setBriefResult] = useState<PrevisitBrief | null>(null)
  const [loading, setLoading] = useState(false)
  const [briefLoading, setBriefLoading] = useState(false)

  const handleQuery = async () => {
    if (!query.trim()) return
    setLoading(true)
    setBriefResult(null)
    try {
      const res = await ragQuery(query, patientId)
      setRagResult(res.data as RAGQueryResponse)
    } catch {
      toast.error('Failed to query clinical history')
    } finally {
      setLoading(false)
    }
  }

  const handleBrief = async () => {
    setBriefLoading(true)
    setRagResult(null)
    try {
      const res = await previsitBrief(patientId)
      setBriefResult(res.data as PrevisitBrief)
    } catch {
      toast.error('Failed to fetch pre-visit brief')
    } finally {
      setBriefLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <h3 className="text-sm font-semibold text-[#052838] mb-3">Ask About This Patient</h3>
        <Textarea
          placeholder="What medications has this patient been prescribed? Any recent diagnoses?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          rows={3}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleQuery()
          }}
        />
        <div className="flex gap-2 mt-3">
          <Button
            onClick={handleQuery}
            loading={loading}
            disabled={!query.trim()}
            size="sm"
            className="flex-1"
          >
            Query History
          </Button>
          <Button
            onClick={handleBrief}
            loading={briefLoading}
            variant="secondary"
            size="sm"
            className="flex-1"
          >
            Pre-visit Brief
          </Button>
        </div>
      </Card>

      {/* RAG Result */}
      {ragResult && (
        <Card className="p-4 space-y-4">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-xs font-sans text-[#5a8898] uppercase tracking-wider">Clinical Answer</h4>
            <div className="flex items-center gap-2">
              {ragResult.cached && <Badge variant="success">Cached</Badge>}
              <span className="text-[10px] font-sans text-[#8aaab8]">
                {ragResult.retrieval_count} sources
              </span>
            </div>
          </div>
          <p className="text-sm text-[#052838] leading-relaxed whitespace-pre-wrap">{ragResult.answer}</p>

          {ragResult.sources?.length > 0 && (
            <div>
              <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Sources</p>
              <div className="space-y-2">
                {ragResult.sources.map((src, i) => (
                  <SourceCard key={i} source={src} />
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Brief Result */}
      {briefResult && (
        <Card className="p-4 space-y-4">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-xs font-sans text-[#5a8898] uppercase tracking-wider">Pre-visit Brief</h4>
            {briefResult.cached && <Badge variant="success">Cached</Badge>}
          </div>
          <p className="text-sm text-[#052838] leading-relaxed whitespace-pre-wrap">{briefResult.brief}</p>

          {briefResult.sources?.length > 0 && (
            <div>
              <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Based On</p>
              <div className="space-y-2">
                {briefResult.sources.map((src, i) => (
                  <SourceCard key={i} source={src} />
                ))}
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}

function SourceCard({ source }: { source: RAGSource }) {
  return (
    <div className="bg-[#e8f2f6] border border-[#c8dde6] rounded-[10px] px-3 py-2.5">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-xs font-sans text-sky">{formatDate(source.visit_date)}</span>
        <span className="text-[10px] font-sans text-[#8aaab8]">
          score: {formatScore(source.rerank_score)}
        </span>
      </div>
      <p className="text-xs text-[#052838] font-medium">{source.diagnosis}</p>
      <p className="text-[10px] text-[#5a8898] mt-0.5">
        {source.visit_type} · Dr. {source.doctor_name}
      </p>
    </div>
  )
}

// ── PDF download helper ──────────────────────────────────────
function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── Vital Signs Charts ───────────────────────────────────────
function VitalSignsCharts({ visits }: { visits: VisitDocument[] }) {
  // Build time-series from visits (oldest first)
  const sorted = [...visits].sort(
    (a, b) => new Date(a.visit_date).getTime() - new Date(b.visit_date).getTime()
  )

  const bpData = sorted
    .filter((v) => v.bp)
    .map((v) => {
      const parts = (v.bp || '').split('/')
      return {
        date: v.visit_date ? String(v.visit_date).slice(0, 10) : '',
        systolic: parts[0] ? parseInt(parts[0]) : null,
        diastolic: parts[1] ? parseInt(parts[1]) : null,
      }
    })
    .filter((d) => d.systolic && d.diastolic)

  const weightData = sorted
    .filter((v) => v.weight_kg)
    .map((v) => ({
      date: v.visit_date ? String(v.visit_date).slice(0, 10) : '',
      weight: v.weight_kg,
    }))

  if (bpData.length < 2 && weightData.length < 2) return null

  // Dynamic recharts import — avoids SSR issues
  const {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  } = require('recharts')

  const chartStyle = {
    fontSize: 10,
    fontFamily: "'Azeret Mono', monospace",
    fill: '#5a8898',
  }

  return (
    <div className="space-y-4">
      {bpData.length >= 2 && (
        <Card className="p-4">
          <p className="text-xs font-sans text-[#5a8898] uppercase tracking-wider mb-3">
            Blood Pressure Trend
          </p>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={bpData} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#c8dde6" />
              <XAxis dataKey="date" tick={chartStyle} tickLine={false} axisLine={false} />
              <YAxis tick={chartStyle} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{ background: '#ffffff', border: '1px solid #c8dde6', borderRadius: 8, fontSize: 11 }}
                labelStyle={{ color: '#5a8898' }}
                itemStyle={{ color: '#38bdf8' }}
              />
              <Legend wrapperStyle={chartStyle} />
              <Line type="monotone" dataKey="systolic" stroke="#38bdf8" strokeWidth={2} dot={{ r: 3, fill: '#38bdf8' }} name="Systolic" />
              <Line type="monotone" dataKey="diastolic" stroke="#22d3ee" strokeWidth={1.5} dot={{ r: 2, fill: '#22d3ee' }} name="Diastolic" strokeDasharray="4 2" />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {weightData.length >= 2 && (
        <Card className="p-4">
          <p className="text-xs font-sans text-[#5a8898] uppercase tracking-wider mb-3">
            Weight Trend (kg)
          </p>
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={weightData} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#c8dde6" />
              <XAxis dataKey="date" tick={chartStyle} tickLine={false} axisLine={false} />
              <YAxis tick={chartStyle} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
              <Tooltip
                contentStyle={{ background: '#ffffff', border: '1px solid #c8dde6', borderRadius: 8, fontSize: 11 }}
                labelStyle={{ color: '#5a8898' }}
                itemStyle={{ color: '#a78bfa' }}
              />
              <Line type="monotone" dataKey="weight" stroke="#a78bfa" strokeWidth={2} dot={{ r: 3, fill: '#a78bfa' }} name="Weight (kg)" />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  )
}

function AddVisitModal({ patientId, open, onClose }: { patientId: string; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [medCount, setMedCount] = useState(1)

  const {
    register,
    handleSubmit,
    watch,
    reset,
    formState: { errors },
  } = useForm<VisitForm>({
    resolver: zodResolver(visitSchema),
    defaultValues: {
      medications: [{ name: '', dose: '', frequency: '', duration: '', notes: '' }],
      followup_required: false,
    },
  })

  const followupRequired = watch('followup_required')

  const mutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => addVisit(patientId, data),
    onSuccess: () => {
      toast.success('Visit recorded successfully')
      queryClient.invalidateQueries({ queryKey: ['patient', patientId] })
      onClose()
      reset()
      setMedCount(1)
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: unknown } } }
      const detail = error?.response?.data?.detail
      const msg = typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
        ? (detail as { msg?: string }[]).map((e) => e.msg || JSON.stringify(e)).join('; ')
        : 'Failed to add visit'
      toast.error(msg)
    },
  })

  const onSubmit = (data: VisitForm) => {
    const payload = {
      ...data,
      medications: data.medications.filter((m) => m.name),
      weight_kg: data.weight_kg ?? undefined,
      bp: data.bp || undefined,
    }
    mutation.mutate(payload as unknown as Record<string, unknown>)
  }

  return (
    <Modal open={open} onClose={onClose} title="Record Visit" size="xl">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <Select
            label="Visit Type"
            options={visitTypeOptions}
            error={errors.visit_type?.message}
            {...register('visit_type')}
          />
          <Input
            label="Blood Pressure (optional)"
            placeholder="120/80"
            error={errors.bp?.message}
            {...register('bp')}
          />
          <Input
            label="Weight (kg)"
            type="number"
            step="1"
            placeholder="65"
            error={errors.weight_kg?.message}
            {...register('weight_kg')}
          />
        </div>

        <Textarea
          label="Chief Complaint"
          placeholder="Patient's main reason for visit"
          rows={2}
          error={errors.chief_complaint?.message}
          {...register('chief_complaint')}
        />

        <Textarea
          label="Symptoms"
          placeholder="Describe symptoms in detail"
          rows={3}
          error={errors.symptoms?.message}
          {...register('symptoms')}
        />

        <Textarea
          label="Diagnosis"
          placeholder="Clinical diagnosis"
          rows={2}
          error={errors.diagnosis?.message}
          {...register('diagnosis')}
        />

        {/* Medications */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-[#5a8898] uppercase tracking-wider">Medications</p>
            <button
              type="button"
              onClick={() => setMedCount((c) => c + 1)}
              className="text-[10px] font-sans text-sky hover:text-sky/80 transition-colors"
            >
              + Add medication
            </button>
          </div>
          <div className="space-y-2">
            {Array.from({ length: medCount }).map((_, i) => (
              <div key={i} className="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-[#e8f2f6] rounded-[10px] p-3">
                <Input
                  placeholder="Drug name"
                  error={errors.medications?.[i]?.name?.message}
                  {...register(`medications.${i}.name`)}
                />
                <Input
                  placeholder="Dose (e.g. 500mg)"
                  error={errors.medications?.[i]?.dose?.message}
                  {...register(`medications.${i}.dose`)}
                />
                <Input
                  placeholder="Frequency"
                  error={errors.medications?.[i]?.frequency?.message}
                  {...register(`medications.${i}.frequency`)}
                />
                <Input
                  placeholder="Duration"
                  error={errors.medications?.[i]?.duration?.message}
                  {...register(`medications.${i}.duration`)}
                />
              </div>
            ))}
          </div>
        </div>

        <Textarea
          label="Clinical Notes (optional)"
          placeholder="Additional notes"
          rows={2}
          {...register('notes')}
        />

        <div className="flex items-center gap-3">
          <input
            type="checkbox"
            id="followup_required"
            className="w-4 h-4 rounded border-[#c8dde6] bg-white accent-sky"
            {...register('followup_required')}
          />
          <label htmlFor="followup_required" className="text-sm text-[#052838] cursor-pointer">
            Follow-up Required
          </label>
        </div>

        {followupRequired && (
          <Input
            label="Follow-up Date"
            type="date"
            error={errors.followup_date?.message}
            {...register('followup_date')}
          />
        )}

        <div className="flex gap-3 pt-2">
          <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
            Cancel
          </Button>
          <Button type="submit" loading={mutation.isPending} className="flex-1">
            Save Visit
          </Button>
        </div>
      </form>
    </Modal>
  )
}

// ---- Main Page ----
export default function PatientDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { user } = useAuthStore()
  const router = useRouter()
  const [showVisitModal, setShowVisitModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)

  const handleExportPatientPdf = useCallback(async () => {
    try {
      const res = await downloadPatientPdf(id)
      triggerBlobDownload(res.data as Blob, `patient_${id}.pdf`)
    } catch {
      toast.error('PDF export failed')
    }
  }, [id])

  const handleEmailPatientPdf = useCallback(async () => {
    try {
      const res = await emailPatientPdf(id)
      toast.success(`Medical history PDF sent to ${res.data.recipient}`)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      toast.error(e?.response?.data?.detail || 'Failed to send email')
    }
  }, [id])

  const { data: patient, isLoading: patientLoading } = useQuery({
    queryKey: ['patient', id],
    queryFn: () => getPatient(id),
    select: (res) => res.data as PatientResponse,
  })

  const canSeeClinical = user?.role === 'doctor' || user?.role === 'admin'

  const { data: visitsData, isLoading: visitsLoading } = useQuery({
    queryKey: ['patient', id, 'visits'],
    queryFn: () => getPatientVisits(id),
    select: (res) => res.data as VisitDocument[],
    enabled: !!patient && canSeeClinical,
  })

  const isLoading = patientLoading || (canSeeClinical && visitsLoading)

  if (patientLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Spinner size="lg" />
      </div>
    )
  }

  if (!patient) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-[#5a8898]">Patient not found</p>
      </div>
    )
  }

  const sortedVisits = [...(visitsData || [])].sort(
    (a, b) => new Date(b.visit_date).getTime() - new Date(a.visit_date).getTime()
  )

  return (
    <div className="space-y-5">
      {/* Patient header */}
      <PatientHeader
        patient={patient}
        onEdit={user?.role === 'admin' || user?.role === 'doctor' ? () => setShowEditModal(true) : undefined}
        onExportPdf={user?.role === 'doctor' ? handleExportPatientPdf : undefined}
        onEmailPdf={user?.role === 'doctor' && patient.email ? handleEmailPatientPdf : undefined}
      />

      {/* Main content — role-specific */}
      {user?.role === 'receptionist' ? (
        /* ── RECEPTIONIST VIEW: personal info only, no clinical data ── */
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          {/* Contact & Identity */}
          <Card className="p-5 space-y-4">
            <h3 className="text-sm font-semibold text-[#052838] border-b border-[#c8dde6] pb-3">
              Contact Information
            </h3>
            <div className="space-y-3">
              {[
                { label: 'Phone', value: patient.phone },
                { label: 'Email', value: patient.email || '—' },
                { label: 'Address', value: patient.address || '—' },
                { label: 'Blood Group', value: patient.blood_group },
                { label: 'Registered', value: formatDate(patient.registered_date) },
              ].map(({ label, value }) => (
                <div key={label} className="flex items-start justify-between gap-4">
                  <span className="text-[11px] font-sans text-[#8aaab8] uppercase tracking-wider flex-shrink-0">{label}</span>
                  <span className="text-sm text-[#052838] text-right">{value}</span>
                </div>
              ))}
            </div>
          </Card>

          {/* Medical alerts (read-only — no diagnosis/visits) */}
          <Card className="p-5 space-y-4">
            <h3 className="text-sm font-semibold text-[#052838] border-b border-[#c8dde6] pb-3">
              Medical Alerts
            </h3>
            <div className="space-y-4">
              <div>
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Known Allergies</p>
                {patient.known_allergies?.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {patient.known_allergies.map((a) => (
                      <Badge key={a} variant="error">{a}</Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[#8aaab8]">None on record</p>
                )}
              </div>
              <div>
                <p className="text-[10px] font-sans text-[#8aaab8] uppercase tracking-wider mb-2">Chronic Conditions</p>
                {patient.chronic_conditions?.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {patient.chronic_conditions.map((c) => (
                      <Badge key={c} variant="warning">{c}</Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-[#8aaab8]">None on record</p>
                )}
              </div>
              <div className="pt-2 border-t border-[#c8dde6] space-y-2">
                <div className="flex justify-between text-xs font-sans">
                  <span className="text-[#8aaab8]">Total Visits</span>
                  <span className="text-sky">{patient.total_visits}</span>
                </div>
                <div className="flex justify-between text-xs font-sans">
                  <span className="text-[#8aaab8]">Last Visit</span>
                  <span className="text-[#052838]">{formatDate(patient.last_visit_date) || '—'}</span>
                </div>
                {patient.pending_followup_date && (
                  <div className="flex justify-between text-xs font-sans">
                    <span className="text-[#8aaab8]">Follow-up Due</span>
                    <span className="text-amber-400">{formatDate(patient.pending_followup_date)}</span>
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>
      ) : (
        /* ── DOCTOR / ADMIN VIEW: full clinical view ── */
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
          {/* Left — visits */}
          <div className="xl:col-span-2 space-y-4">
            {/* Vital signs charts — shown if ≥2 data points */}
            {sortedVisits.length >= 2 && (
              <VitalSignsCharts visits={sortedVisits} />
            )}

            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-[#052838]">
                Visit History
                <span className="ml-2 font-sans text-sky">{sortedVisits.length}</span>
              </h3>
              {user?.role === 'doctor' && (
                <Button size="sm" onClick={() => setShowVisitModal(true)}>
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                  Add Visit
                </Button>
              )}
            </div>

            {sortedVisits.length === 0 ? (
              <Card className="p-8 text-center">
                <p className="text-[#8aaab8] text-sm">No visits recorded yet</p>
              </Card>
            ) : (
              <div className="space-y-0 ml-2">
                {sortedVisits.map((visit) => (
                  <VisitCard
                    key={visit.id}
                    visit={visit}
                    patientId={id}
                    canEdit={user?.role === 'admin' || user?.role === 'doctor'}
                    canExport={user?.role === 'doctor'}
                    patientEmail={user?.role === 'doctor' ? patient.email : null}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Right — RAG panel */}
          <div>
            <RAGPanel patientId={id} />
          </div>
        </div>
      )}

      {/* Add Visit Modal */}
      <AddVisitModal
        patientId={id}
        open={showVisitModal}
        onClose={() => setShowVisitModal(false)}
      />

      {/* Edit Patient Modal */}
      {showEditModal && (
        <EditPatientModal
          patient={patient}
          open={showEditModal}
          onClose={() => setShowEditModal(false)}
        />
      )}
    </div>
  )
}
