# Unit Test Suite

Comprehensive unit testing suite for AI Cold Emailer with focus on edge cases and behavior validation.

## Structure

```
tests/
├── unit/
│   ├── backend/          # Python/pytest tests
│   │   ├── test_csv_processor.py
│   │   ├── test_rate_limiter.py
│   │   └── test_text_cleaner.py
│   └── frontend/         # JavaScript/Vitest tests
│       └── api.test.js
├── conftest.py           # Pytest configuration
└── pytest.ini            # Pytest settings
```

## Backend Tests (Python/Pytest)

### Setup

```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt  # Includes pytest
```

### Run Tests

```bash
# Run all tests
pytest

# Run from project root
cd /path/to/ColdEmailer
pytest tests/

# Run specific test file
pytest tests/unit/backend/test_csv_processor.py

# Run with coverage
pytest --cov=backend --cov-report=html

# Run in watch mode
pytest-watch
```

### Test Coverage

- **CSVProcessor**: 25+ tests covering:
  - Empty files, missing data
  - Null/undefined values
  - Large datasets
  - Concurrent operations
  - Edge cases (empty IDs, invalid data)

- **RateLimiter**: 15+ tests covering:
  - Time-based limits
  - Concurrent operations
  - Edge cases (exceeding limits, empty state)
  - Usage statistics

- **TextCleaner**: 15+ tests covering:
  - Empty/null inputs
  - Very large text
  - Unicode characters
  - Special characters
  - Navigation word removal
  - Repetition removal

## Frontend Tests (JavaScript/Vitest)

### Setup

```bash
cd frontend
npm install
```

### Run Tests

```bash
# Run all tests
npm run test

# Watch mode
npm run test:watch

# With coverage
npm run test:coverage
```

### Test Coverage

- **API Client**: 30+ tests covering:
  - All API endpoints
  - Request/response handling
  - Error cases (network, 404, 500)
  - Edge cases (null, empty arrays, undefined)
  - Interceptors

## Test Quality Standards

All tests follow these principles:

1. **Behavior-focused**: Tests assert actual behavior, not just existence
2. **Edge case coverage**: Every function tested with:
   - null/undefined
   - Empty strings/arrays
   - Very large inputs
   - Incorrect types
   - Duplicate calls

3. **Isolation**: Each test is independent
4. **Real failures**: Tests will fail if logic breaks

## Adding New Tests

### Backend

Create `tests/unit/backend/test_<module>.py`:

```python
import pytest
from backend.your_module import YourClass

class TestYourClass:
    def test_normal_case(self):
        # Test normal behavior
        pass
    
    def test_edge_case_null(self):
        # Test with null
        pass
    
    def test_edge_case_empty(self):
        # Test with empty values
        pass
```

### Frontend

Create `tests/unit/frontend/<module>.test.js`:

```javascript
import { describe, it, expect } from 'vitest'

describe('YourModule', () => {
  it('handles normal case', () => {
    // Test normal behavior
  })
  
  it('handles null', () => {
    // Test with null
  })
})
```

## Continuous Integration

Tests are designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Run backend tests
  run: |
    cd backend
    source venv/bin/activate
    pytest tests/ --cov=backend

- name: Run frontend tests
  run: |
    cd frontend
    npm run test
```

## Troubleshooting

**Import errors in backend tests:**
- Ensure `conftest.py` adds backend to path
- Run from project root: `pytest tests/`

**Frontend tests not finding modules:**
- Check `vite.config.js` has test configuration
- Ensure `tests/setup.js` exists

**Coverage not working:**
- Install coverage tool: `pip install pytest-cov`
- For frontend: `npm install --save-dev @vitest/coverage-v8`
