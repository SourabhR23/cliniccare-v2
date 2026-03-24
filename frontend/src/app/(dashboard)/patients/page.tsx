'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { toast } from 'sonner'
import { listPatients, searchPatients, createPatient, listDoctors } from '@/lib/api'
import { useAuthStore } from '@/store/auth'
import { formatDate, cn } from '@/lib/utils'
import { PatientListItem, Doctor } from '@/types'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/Input'
import { Spinner } from '@/components/ui/Spinner'
import { Modal } from '@/components/ui/Modal'

// Schema
const patientSchema = z.object({
  name: z.string().min(2, 'Name is required'),
  date_of_birth: z.string().min(1, 'Date of birth is required'),
  sex: z.enum(['M', 'F', 'O'], { required_error: 'Sex is required' }),
  blood_group: z.string().min(1, 'Blood group is required'),
  phone: z.string().min(6, 'Phone is required'),
  email: z.string().min(1, 'Email is required').email('Valid email is required'),
  address: z.string().optional(),
  known_allergies: z.string().optional(),
  chronic_conditions: z.string().optional(),
  assigned_doctor_id: z.string().min(1, 'Assign a doctor'),
})

type PatientForm = z.infer<typeof patientSchema>

type FilterType = 'all' | 'followup' | 'new'

function AllergyList({ items, variant }: { items: string[]; variant: 'allergy' | 'condition' }) {
  if (!items || items.length === 0) return <span className="text-[#8aaab8] text-xs">—</span>
  const shown = items.slice(0, 2)
  const extra = items.length - 2
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((item) => (
        <Badge key={item} variant={variant} className="text-[9px]">
          {item}
        </Badge>
      ))}
      {extra > 0 && (
        <Badge variant="muted" className="text-[9px]">
          +{extra}
        </Badge>
      )}
    </div>
  )
}

export default function PatientsPage() {
  const { user } = useAuthStore()
  const router = useRouter()
  const queryClient = useQueryClient()

  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [filter, setFilter] = useState<FilterType>('all')
  const [showModal, setShowModal] = useState(false)

  // Debounce
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(timer)
  }, [search])

  const { data: allPatients, isLoading: loadingAll } = useQuery({
    queryKey: ['patients', 'list'],
    queryFn: () => listPatients(0, 100),
    select: (res) => res.data as PatientListItem[],
    enabled: !debouncedSearch,
  })

  const { data: searchResults, isLoading: loadingSearch } = useQuery({
    queryKey: ['patients', 'search', debouncedSearch],
    queryFn: () => searchPatients(debouncedSearch),
    select: (res) => res.data as PatientListItem[],
    enabled: !!debouncedSearch,
  })

  const patients = debouncedSearch ? (searchResults || []) : (allPatients || [])
  const isLoading = debouncedSearch ? loadingSearch : loadingAll

  // Apply filter
  const filtered = patients.filter((p) => {
    if (filter === 'followup') return !!p.pending_followup_date
    if (filter === 'new') return p.total_visits === 0 || p.total_visits === 1
    return true
  })

  // Doctors list
  const { data: doctors } = useQuery({
    queryKey: ['doctors'],
    queryFn: () => listDoctors(),
    select: (res) => res.data as Doctor[],
  })

  // Create patient mutation
  const createMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => createPatient(data),
    onSuccess: () => {
      toast.success('Patient registered successfully')
      queryClient.invalidateQueries({ queryKey: ['patients'] })
      setShowModal(false)
      reset()
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: unknown } } }
      const detail = error?.response?.data?.detail
      const msg = typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
        ? (detail as { msg?: string }[]).map((e) => e.msg || JSON.stringify(e)).join('; ')
        : 'Failed to register patient'
      toast.error(msg)
    },
  })

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<PatientForm>({
    resolver: zodResolver(patientSchema),
  })

  const onSubmit = (data: PatientForm) => {
    const payload = {
      personal: {
        name: data.name,
        date_of_birth: data.date_of_birth,
        sex: data.sex,
        blood_group: data.blood_group,
        phone: data.phone,
        email: data.email,
        address: data.address || null,
        known_allergies: data.known_allergies
          ? data.known_allergies.split(',').map((s) => s.trim()).filter(Boolean)
          : [],
        chronic_conditions: data.chronic_conditions
          ? data.chronic_conditions.split(',').map((s) => s.trim()).filter(Boolean)
          : [],
        assigned_doctor_id: data.assigned_doctor_id,
      },
    }
    createMutation.mutate(payload)
  }

  const sexOptions = [
    { value: '', label: 'Select sex' },
    { value: 'M', label: 'Male' },
    { value: 'F', label: 'Female' },
    { value: 'O', label: 'Other' },
  ]

  const bloodGroupOptions = [
    { value: '', label: 'Select blood group' },
    ...['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-'].map((v) => ({ value: v, label: v })),
  ]

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold text-[#052838]">Patients</h2>
          <p className="text-sm text-[#5a8898] mt-0.5">
            {filtered.length} {filtered.length === 1 ? 'patient' : 'patients'}
            {debouncedSearch && ` matching "${debouncedSearch}"`}
          </p>
        </div>
        <Button onClick={() => setShowModal(true)} size="md">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Register Patient
        </Button>
      </div>

      {/* Search + Filters */}
      <Card className="p-4 flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <svg
            className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8aaab8]"
            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, phone..."
            className="w-full bg-white text-[#052838] placeholder-[#8aaab8] border border-[#c8dde6] rounded-[10px] pl-10 pr-4 py-2.5 text-sm focus:outline-none focus:border-sky/50 focus:ring-1 focus:ring-sky/20 transition-all"
          />
          {isLoading && (
            <div className="absolute right-3.5 top-1/2 -translate-y-1/2">
              <Spinner size="sm" />
            </div>
          )}
        </div>

        <div className="flex gap-2">
          {(['all', 'followup', 'new'] as FilterType[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={cn(
                'px-3 py-2 rounded-[8px] text-xs font-sans font-medium uppercase tracking-wider transition-all',
                filter === f
                  ? 'bg-sky/10 text-sky border border-sky/20'
                  : 'text-[#5a8898] hover:text-[#052838] border border-transparent hover:border-[#c8dde6]'
              )}
            >
              {f === 'followup' ? 'Follow-up' : f}
            </button>
          ))}
        </div>
      </Card>

      {/* Table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[800px]">
            <thead>
              <tr className="border-b border-[#c8dde6]">
                {['Name', 'Age/Sex', 'Phone', 'Blood', 'Allergies', 'Conditions', 'Visits', 'Last Visit', 'Follow-up', ''].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left text-[10px] font-sans text-[#8aaab8] uppercase tracking-widest whitespace-nowrap"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="animate-pulse border-b border-[#c8dde6]">
                    {Array.from({ length: 10 }).map((_, j) => (
                      <td key={j} className="px-4 py-3.5">
                        <div className="h-3 bg-[#e8f2f6] rounded w-3/4" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : filtered.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-12 text-center">
                    <p className="text-[#8aaab8] text-sm">
                      {debouncedSearch ? `No patients found for "${debouncedSearch}"` : 'No patients found'}
                    </p>
                  </td>
                </tr>
              ) : (
                filtered.map((p) => (
                  <tr
                    key={p.id}
                    onClick={() => router.push(`/patients/${p.id}`)}
                    className="border-b border-[#c8dde6] hover:bg-[#e8f2f6] cursor-pointer transition-colors group"
                  >
                    <td className="px-4 py-3.5">
                      <p className="text-sm font-medium text-[#052838] group-hover:text-sky transition-colors">{p.name}</p>
                    </td>
                    <td className="px-4 py-3.5 font-sans text-xs text-[#5a8898] whitespace-nowrap">
                      {p.age}y / {p.sex}
                    </td>
                    <td className="px-4 py-3.5 font-sans text-xs text-[#5a8898]">{p.phone}</td>
                    <td className="px-4 py-3.5">
                      <Badge variant="default" className="text-[9px]">{p.blood_group}</Badge>
                    </td>
                    <td className="px-4 py-3.5">
                      <AllergyList items={p.known_allergies} variant="allergy" />
                    </td>
                    <td className="px-4 py-3.5">
                      <AllergyList items={p.chronic_conditions} variant="condition" />
                    </td>
                    <td className="px-4 py-3.5 font-sans text-sm text-sky">{p.total_visits}</td>
                    <td className="px-4 py-3.5 font-sans text-xs text-[#5a8898] whitespace-nowrap">
                      {formatDate(p.last_visit_date)}
                    </td>
                    <td className="px-4 py-3.5 font-sans text-xs whitespace-nowrap">
                      {p.pending_followup_date ? (
                        <span className="text-yellow-400">{formatDate(p.pending_followup_date)}</span>
                      ) : (
                        <span className="text-[#8aaab8]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5">
                      <svg
                        className="w-4 h-4 text-[#8aaab8] group-hover:text-sky transition-colors"
                        fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Register Patient Modal */}
      <Modal
        open={showModal}
        onClose={() => { setShowModal(false); reset() }}
        title="Register New Patient"
        size="lg"
      >
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="Full Name"
              placeholder="John Doe"
              error={errors.name?.message}
              containerClassName="col-span-2"
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
              options={sexOptions}
              error={errors.sex?.message}
              {...register('sex')}
            />
            <Select
              label="Blood Group"
              options={bloodGroupOptions}
              error={errors.blood_group?.message}
              {...register('blood_group')}
            />
            <Input
              label="Phone"
              placeholder="+91 98765 43210"
              error={errors.phone?.message}
              {...register('phone')}
            />
            <Input
              label="Email"
              type="email"
              placeholder="patient@email.com"
              error={errors.email?.message}
              containerClassName="col-span-2 sm:col-span-1"
              {...register('email')}
            />
            <Input
              label="Address (optional)"
              placeholder="123 Main St, City"
              containerClassName="col-span-2 sm:col-span-1"
              {...register('address')}
            />
            <Input
              label="Known Allergies (comma-separated)"
              placeholder="Penicillin, Aspirin"
              containerClassName="col-span-2"
              {...register('known_allergies')}
            />
            <Input
              label="Chronic Conditions (comma-separated)"
              placeholder="Diabetes, Hypertension"
              containerClassName="col-span-2"
              {...register('chronic_conditions')}
            />
            <Select
              label="Assign Doctor"
              options={[
                { value: '', label: 'Select doctor' },
                ...(doctors || []).map((d) => ({
                  value: d.id,
                  label: `${d.name}${d.specialization ? ` — ${d.specialization}` : ''}`,
                })),
              ]}
              error={errors.assigned_doctor_id?.message}
              containerClassName="col-span-2"
              {...register('assigned_doctor_id')}
            />
          </div>

          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => { setShowModal(false); reset() }}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              loading={createMutation.isPending}
              className="flex-1"
            >
              Register Patient
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
