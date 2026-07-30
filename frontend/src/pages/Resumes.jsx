/* Resume versions: upload PDFs, label them, pick a default */
import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  FileText, Star, Trash2, Download, Pencil, UploadCloud, Check,
  Eye, ExternalLink,
} from 'lucide-react'
import { resumesAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, Modal, fmtDate } from '../ui'

export default function Resumes() {
  const [resumes, setResumes] = useState(null)
  const [drag, setDrag] = useState(false)
  const [renaming, setRenaming] = useState(null)
  const [previewing, setPreviewing] = useState(null)
  const inputRef = useRef(null)

  const load = useCallback(async () => {
    try { setResumes((await resumesAPI.list()).data) }
    catch (e) { toast.error(errMessage(e)); setResumes([]) }
  }, [])

  useEffect(() => { load() }, [load])

  const upload = async (files) => {
    for (const file of files) {
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        toast.error(`${file.name}: only PDF resumes are supported`)
        continue
      }
      const t = toast.loading(`Uploading ${file.name}…`)
      try {
        await resumesAPI.upload(file)
        toast.success(`${file.name} uploaded and parsed`, { id: t })
      } catch (e) {
        toast.error(errMessage(e, 'Upload failed'), { id: t })
      }
    }
    load()
  }

  const setDefault = async (r) => {
    try {
      await resumesAPI.update(r.id, { is_default: true })
      toast.success(`${r.label} is now the default`)
      load()
    } catch (e) { toast.error(errMessage(e)) }
  }

  const remove = async (r) => {
    if (!window.confirm(`Delete ${r.label}? Emails already sent keep their record.`)) return
    try {
      await resumesAPI.delete(r.id)
      toast.success('Resume deleted')
      load()
    } catch (e) {
      // 409 = unsent drafts still reference this PDF; make the trade-off explicit
      if (e?.response?.status === 409) {
        if (window.confirm(`${errMessage(e)}\n\nDelete anyway? Those drafts will attach your default resume instead.`)) {
          try {
            await resumesAPI.delete(r.id, true)
            toast.success('Resume deleted')
            load()
          } catch (e2) { toast.error(errMessage(e2)) }
        }
      } else {
        toast.error(errMessage(e))
      }
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Resumes</div>
          <div className="page-desc">
            Upload versions of your resume — the AI reads them to personalize emails, and attaches the PDF when you send
          </div>
        </div>
      </div>

      <div
        className={`dropzone ${drag ? 'drag' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); upload([...e.dataTransfer.files]) }}
      >
        <UploadCloud size={26} style={{ marginBottom: 8 }} />
        <div style={{ fontWeight: 650 }}>Drop a PDF here or click to upload</div>
        <div className="tiny mt-8" style={{ marginTop: 4 }}>Keep versions for different roles — “ML research”, “Full-stack”, “Sales”…</div>
        <input ref={inputRef} type="file" accept=".pdf" multiple hidden
          onChange={(e) => { upload([...e.target.files]); e.target.value = '' }} />
      </div>

      <div className="mt-24">
        {resumes === null ? (
          <div className="resume-grid">
            {[...Array(2)].map((_, i) => <div key={i} className="skeleton" style={{ height: 150 }} />)}
          </div>
        ) : resumes.length === 0 ? (
          <div className="card">
            <EmptyState icon={FileText} title="No resumes yet"
              desc="Upload at least one so the AI can weave your real experience into every email." />
          </div>
        ) : (
          <div className="resume-grid">
            {resumes.map((r) => (
              <div key={r.id} className="card resume-card stack" style={{ gap: 12 }}>
                <div className="row-between">
                  <div className="resume-icon"><FileText size={18} /></div>
                  {r.is_default
                    ? <Chip tone="accent"><Star size={10} /> default</Chip>
                    : <Button size="sm" variant="ghost" onClick={() => setDefault(r)}>Make default</Button>}
                </div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14.5 }}>{r.label}</div>
                  <div className="tiny">{r.filename} · {fmtDate(r.uploaded_at)}</div>
                  {!r.has_text && (
                    <div className="tiny mt-8" style={{ color: 'var(--amber)' }}>
                      ⚠ Couldn't extract text — AI will rely on your profile background instead
                    </div>
                  )}
                </div>
                {r.text_preview && (
                  <div className="tiny" style={{
                    maxHeight: 58, overflow: 'hidden', lineHeight: 1.5,
                    maskImage: 'linear-gradient(#000 60%, transparent)',
                  }}>
                    {r.text_preview}
                  </div>
                )}
                <div className="row" style={{ gap: 4 }}>
                  <button className="icon-btn" title="Rename" onClick={() => setRenaming(r)}><Pencil size={14} /></button>
                  <button className="icon-btn" title="Preview PDF" aria-label={`Preview ${r.label}`}
                    onClick={() => setPreviewing(r)}>
                    <Eye size={14} />
                  </button>
                  <button className="icon-btn" title="Download PDF" aria-label={`Download ${r.label}`}
                    onClick={() => window.open(resumesAPI.downloadUrl(r.id), '_blank')}>
                    <Download size={14} />
                  </button>
                  <button className="icon-btn danger" title="Delete" onClick={() => remove(r)}><Trash2 size={14} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {renaming && (
        <RenameModal resume={renaming} onClose={() => setRenaming(null)}
          onSaved={() => { setRenaming(null); load() }} />
      )}
      {previewing && (
        <Modal title={previewing.label} onClose={() => setPreviewing(null)} large
          footer={<>
            <Button variant="ghost" icon={ExternalLink}
              onClick={() => window.open(resumesAPI.fileUrl(previewing.id), '_blank')}>
              Open in new tab
            </Button>
            <Button icon={Download}
              onClick={() => window.open(resumesAPI.downloadUrl(previewing.id), '_blank')}>
              Download
            </Button>
          </>}>
          <iframe
            className="pdf-preview"
            title={`${previewing.label} PDF preview`}
            src={resumesAPI.fileUrl(previewing.id)}
          />
        </Modal>
      )}
    </div>
  )
}

function RenameModal({ resume, onClose, onSaved }) {
  const [label, setLabel] = useState(resume.label)
  const save = async () => {
    if (!label.trim()) return
    try {
      await resumesAPI.update(resume.id, { label: label.trim() })
      toast.success('Renamed')
      onSaved()
    } catch (e) { toast.error(errMessage(e)) }
  }
  return (
    <Modal title="Rename resume" onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" icon={Check} onClick={save} disabled={!label.trim()}>Save</Button>
      </>}>
      <div className="field">
        <div className="field-label">Label</div>
        <input className="input" autoFocus value={label} onChange={(e) => setLabel(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
          placeholder='e.g. "ML research", "2029 general"' />
        <div className="field-hint">Shown when picking which version to use for a batch of emails.</div>
      </div>
    </Modal>
  )
}
