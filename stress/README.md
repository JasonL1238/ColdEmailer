# Stress Testing Suite

Complete stress testing system for AI Cold Emailer to verify reliability under heavy usage.

## Setup

1. Install k6 (for load testing):
   ```bash
   # macOS
   brew install k6
   
   # Linux
   sudo gpg -k
   sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
   echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
   sudo apt-get update
   sudo apt-get install k6
   
   # Windows
   choco install k6
   ```

2. Install Node.js dependencies:
   ```bash
   cd stress
   npm install
   ```

## Load Testing

Test the API under different load levels:

### Light Load (10 users)
```bash
npm run stress:light
# or
k6 run k6-load-test.js
```

### Medium Load (50 users)
```bash
npm run stress:medium
# or
k6 run k6-load-test-medium.js
```

### Heavy Load (200 users)
```bash
npm run stress:heavy
# or
k6 run k6-load-test-heavy.js
```

**Metrics measured:**
- Response time (p95, p99)
- Error rate
- Requests per second
- Timeouts

## Concurrency Safety Test

Tests that simultaneous requests don't overwrite each other:

```bash
npm run concurrency
# or
node concurrency-test.js
```

**What it checks:**
- Requests don't overwrite each other
- Sessions stay isolated
- No shared global state corruption
- All contacts are created with unique IDs

## Large Input Testing

Tests server behavior with extremely large payloads:

```bash
npm run large-input
# or
node large-input-test.js
```

**What it tests:**
- Small inputs (100 chars)
- Medium inputs (1K chars)
- Large inputs (10K chars)
- Very large inputs (100K chars)
- Extremely large inputs (1M chars)
- Large JSON arrays

**Verifies:**
- Server doesn't crash
- Graceful rejection with proper errors
- Useful error messages

## Memory Leak Detection

Runs the server under continuous load and monitors memory:

```bash
# Set backend PID to monitor (optional)
export BACKEND_PID=$(pgrep -f "uvicorn main:app")
npm run memory-leak
# or
node memory-leak-test.js
```

**What it monitors:**
- Memory usage over time
- Heap growth
- Open handles
- Memory trends

**Duration:** 5 minutes of continuous requests

**Output:** `memory-log.json` with timestamped memory readings

## Slow API Simulation

Enable stress test mode to simulate slow/failing external APIs:

```bash
# In backend/.env or environment
STRESS_TEST_MODE=true

# Then start backend
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

**What it does:**
- Randomly delays responses (0-5 seconds)
- Randomly fails 10% of requests
- Simulates LLM or external API instability

## Custom Configuration

Set custom base URL:
```bash
BASE_URL=http://localhost:8000 npm run stress:light
```

## Expected Results

### Load Tests
- **Light:** Should handle 10 users easily, <500ms response time
- **Medium:** May see some slowdown, <1000ms response time
- **Heavy:** May see errors/timeouts, <2000ms response time

### Concurrency Test
- All requests should succeed
- All contacts should have unique IDs
- No data corruption

### Large Input Test
- Small/medium inputs: Should succeed
- Large inputs: Should be rejected gracefully with 400/413 errors
- Server should not crash

### Memory Leak Test
- Memory should stabilize after initial growth
- No continuous upward trend
- Warning if memory increases >20% over time

## Troubleshooting

**k6 not found:**
- Install k6 using instructions above
- Verify: `k6 version`

**Backend not responding:**
- Ensure backend is running: `http://localhost:8000`
- Check backend logs for errors

**Memory test not working:**
- Set `BACKEND_PID` environment variable
- Find PID: `pgrep -f "uvicorn main:app"`

**Tests failing:**
- Check backend is running
- Check CORS settings
- Review backend logs for errors
