import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const responseTime = new Trend('response_time');

// Test configuration
export const options = {
  stages: [
    { duration: '30s', target: 10 },   // Ramp up to 10 users
    { duration: '1m', target: 10 },   // Stay at 10 users
    { duration: '30s', target: 0 },   // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% of requests should be below 500ms
    errors: ['rate<0.1'],              // Error rate should be less than 10%
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Test GET /api/contacts
  let res = http.get(`${BASE_URL}/api/contacts`);
  let success = check(res, {
    'contacts status is 200': (r) => r.status === 200,
    'contacts response time < 500ms': (r) => r.timings.duration < 500,
  });
  errorRate.add(!success);
  responseTime.add(res.timings.duration);
  sleep(1);

  // Test POST /api/contacts (create)
  const contactPayload = JSON.stringify({
    name: `Test User ${__VU}-${__ITER}`,
    company: `Test Company ${__VU}`,
    email: `test${__VU}-${__ITER}@example.com`,
    status: 'pending',
  });
  res = http.post(`${BASE_URL}/api/contacts`, contactPayload, {
    headers: { 'Content-Type': 'application/json' },
  });
  success = check(res, {
    'create contact status is 200': (r) => r.status === 200,
    'create contact response time < 1000ms': (r) => r.timings.duration < 1000,
  });
  errorRate.add(!success);
  responseTime.add(res.timings.duration);
  sleep(1);

  // Test GET /api/usage
  res = http.get(`${BASE_URL}/api/usage`);
  success = check(res, {
    'usage status is 200': (r) => r.status === 200,
  });
  errorRate.add(!success);
  responseTime.add(res.timings.duration);
  sleep(1);
}
