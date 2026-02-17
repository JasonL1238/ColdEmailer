import { describe, it, expect, vi, beforeEach } from 'vitest'

// Use vi.hoisted to create mock instance that can be accessed
const { mockAxiosInstance } = vi.hoisted(() => {
  const instance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() }
    }
  }
  return { mockAxiosInstance: instance }
})

// Mock axios
vi.mock('axios', () => {
  return {
    default: {
      create: vi.fn(() => mockAxiosInstance)
    }
  }
})

// Import after mocking
import { contactsAPI, companyAPI, emailAPI, usageAPI } from '../../src/api.js'

describe('API Client', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('contactsAPI', () => {
    it('getAll calls correct endpoint with status param', async () => {
      const mockResponse = { data: [{ id: '1', name: 'Test' }] }
      mockAxiosInstance.get.mockResolvedValue(mockResponse)

      const result = await contactsAPI.getAll('pending')

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/contacts', { params: { status: 'pending' } })
      expect(result).toEqual(mockResponse)
    })

    it('getAll handles errors correctly', async () => {
      const mockError = new Error('Network error')
      mockAxiosInstance.get.mockRejectedValue(mockError)

      await expect(contactsAPI.getAll()).rejects.toThrow('Network error')
    })

    it('create sends POST with contact data', async () => {
      const contact = { name: 'John', company: 'Acme', email: 'john@acme.com' }
      const mockResponse = { data: { id: '1', ...contact } }
      mockAxiosInstance.post.mockResolvedValue(mockResponse)

      const result = await contactsAPI.create(contact)

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/contacts', contact)
      expect(result).toEqual(mockResponse)
    })

    it('update sends PUT with updates', async () => {
      const updates = { name: 'Updated Name' }
      const mockResponse = { data: { id: '1', name: 'Updated Name' } }
      mockAxiosInstance.put.mockResolvedValue(mockResponse)

      const result = await contactsAPI.update('1', updates)

      expect(mockAxiosInstance.put).toHaveBeenCalledWith('/contacts/1', updates)
      expect(result).toEqual(mockResponse)
    })

    it('delete sends DELETE request', async () => {
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })

      await contactsAPI.delete('1')

      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/contacts/1')
    })

    it('bulkDelete sends DELETE with data', async () => {
      const ids = ['1', '2', '3']
      mockAxiosInstance.delete.mockResolvedValue({ data: { success: true } })

      await contactsAPI.bulkDelete(ids)

      expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/contacts/bulk', { data: ids })
    })

    it('upload sends FormData', async () => {
      const file = new File(['content'], 'test.csv', { type: 'text/csv' })
      const mockResponse = { data: { success: true } }
      mockAxiosInstance.post.mockResolvedValue(mockResponse)

      const result = await contactsAPI.upload(file)

      expect(mockAxiosInstance.post).toHaveBeenCalledWith(
        '/upload',
        expect.any(FormData),
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      expect(result).toEqual(mockResponse)
    })

    it('export requests blob response', async () => {
      const blob = new Blob(['csv,data'])
      mockAxiosInstance.get.mockResolvedValue({ data: blob })

      const result = await contactsAPI.export()

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/contacts/export', { responseType: 'blob' })
      expect(result.data).toBe(blob)
    })
  })

  describe('companyAPI', () => {
    it('enrich sends POST with company name', async () => {
      const mockResponse = { data: { name: 'Acme', summary: 'Test' } }
      mockAxiosInstance.post.mockResolvedValue(mockResponse)

      const result = await companyAPI.enrich('Acme Corp')

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/enrich-company', null, {
        params: { company_name: 'Acme Corp' }
      })
      expect(result).toEqual(mockResponse)
    })

    it('enrich includes URL when provided', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: {} })

      await companyAPI.enrich('Acme Corp', 'https://acme.com')

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/enrich-company', null, {
        params: { company_name: 'Acme Corp', url: 'https://acme.com' }
      })
    })

    it('getMetadata sends GET request', async () => {
      const mockResponse = { data: { name: 'Acme', industry: 'Tech' } }
      mockAxiosInstance.get.mockResolvedValue(mockResponse)

      const result = await companyAPI.getMetadata('Acme Corp')

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/company-metadata/Acme Corp')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('emailAPI', () => {
    it('generate sends POST with contact IDs', async () => {
      const mockResponse = { data: [{ id: 'email1', subject: 'Test' }] }
      mockAxiosInstance.post.mockResolvedValue(mockResponse)

      const result = await emailAPI.generate(['1', '2'])

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/generate-emails', {
        contact_ids: ['1', '2'],
        user_name: null,
        user_background: null,
        user_email: null
      })
      expect(result).toEqual(mockResponse)
    })

    it('generate includes user info when provided', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: [] })

      await emailAPI.generate(null, 'John Doe', 'Engineer', 'john@test.com')

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/generate-emails', {
        contact_ids: null,
        user_name: 'John Doe',
        user_background: 'Engineer',
        user_email: 'john@test.com'
      })
    })

    it('getAll filters by status', async () => {
      const mockResponse = { data: [{ id: '1', status: 'pending' }] }
      mockAxiosInstance.get.mockResolvedValue(mockResponse)

      const result = await emailAPI.getAll('pending')

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/emails', { params: { status: 'pending' } })
      expect(result).toEqual(mockResponse)
    })

    it('updateStatus sends PUT with status', async () => {
      mockAxiosInstance.put.mockResolvedValue({ data: { success: true } })

      await emailAPI.updateStatus('email1', 'accepted')

      expect(mockAxiosInstance.put).toHaveBeenCalledWith('/emails/email1', null, {
        params: { status: 'accepted' }
      })
    })

    it('send sends POST with email IDs', async () => {
      const emailIds = ['1', '2', '3']
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })

      await emailAPI.send(emailIds)

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/send-emails', { email_ids: emailIds })
    })
  })

  describe('usageAPI', () => {
    it('getStats sends GET request', async () => {
      const mockResponse = { data: { emails_sent_today: 5 } }
      mockAxiosInstance.get.mockResolvedValue(mockResponse)

      const result = await usageAPI.getStats()

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/usage')
      expect(result).toEqual(mockResponse)
    })
  })

  describe('Error handling', () => {
    it('handles network errors', async () => {
      const networkError = { message: 'Network Error', code: 'ERR_NETWORK' }
      mockAxiosInstance.get.mockRejectedValue(networkError)

      await expect(contactsAPI.getAll()).rejects.toEqual(networkError)
    })

    it('handles 404 errors', async () => {
      const notFoundError = {
        response: { status: 404, data: { detail: 'Not found' } }
      }
      mockAxiosInstance.get.mockRejectedValue(notFoundError)

      await expect(contactsAPI.getAll()).rejects.toEqual(notFoundError)
    })

    it('handles 500 errors', async () => {
      const serverError = {
        response: { status: 500, data: { detail: 'Internal server error' } }
      }
      mockAxiosInstance.post.mockRejectedValue(serverError)

      await expect(contactsAPI.create({})).rejects.toEqual(serverError)
    })
  })

  describe('Edge cases', () => {
    it('handles null contact IDs', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: [] })

      await emailAPI.generate(null)

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/generate-emails', {
        contact_ids: null,
        user_name: null,
        user_background: null,
        user_email: null
      })
    })

    it('handles empty arrays', async () => {
      mockAxiosInstance.post.mockResolvedValue({ data: { success: true } })

      await emailAPI.send([])

      expect(mockAxiosInstance.post).toHaveBeenCalledWith('/send-emails', { email_ids: [] })
    })

    it('handles undefined status', async () => {
      mockAxiosInstance.get.mockResolvedValue({ data: [] })

      await contactsAPI.getAll(undefined)

      expect(mockAxiosInstance.get).toHaveBeenCalledWith('/contacts', { params: { status: undefined } })
    })
  })
})
