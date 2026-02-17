# Resume Attachment Setup

Your resume will be automatically attached to every email you send.

## Quick Setup

1. **Place your resume file** in the project root directory
   - Name it something like `resume.pdf` or `Jason_Li_Resume.pdf`
   - PDF format is recommended

2. **Update `.env` file**:
   ```bash
   RESUME_PATH=resume.pdf
   ```
   Or use full path:
   ```bash
   RESUME_PATH=/Users/jasonli/Documents/GitHub/ColdEmailer/resume.pdf
   ```

3. **Update Settings in UI** (optional, for reference):
   - Go to "Review Emails" tab
   - Click "⚙️ Settings"
   - Enter the resume path in the "Resume File Path" field
   - This is just for your reference - the actual path is read from `.env`

## Default Settings

- **Name**: Jason Li (pre-filled in settings)
- **Email**: jason.ye.li.7@gmail.com (pre-filled in settings)
- **Resume**: Will be attached automatically if `RESUME_PATH` is set in `.env`

## Testing

To verify resume attachment works:
1. Generate a test email
2. Accept it
3. Send it to yourself first to check the attachment
4. If successful, proceed with sending to contacts

## Troubleshooting

**Resume not attaching?**
- Check that `RESUME_PATH` in `.env` points to the correct file
- Verify the file exists at that path
- Check file permissions (should be readable)
- Look for error messages in backend console

**File not found error?**
- Use absolute path instead of relative path
- Make sure there are no spaces in the filename (or quote the path)
- Check that the file extension matches (.pdf, .docx, etc.)
