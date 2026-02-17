import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const responseTime = new Trend('response_time');

export const options = {
  stages: [
    { duration: '2m', target: 200 },  // Ramp up to 200 users
    { duration: '3m', target: 200 },   // Stay at 200 users
    { duration: '2m', target: 0 },     // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    errors: ['rate<0.2'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  let res = http.get(`${BASE_URL}/api/contacts`);
  let success = check(res, {
    'contacts status is 200': (r) => r.status === 200,
  });
  errorRate.add(!success);
  responseTime.add(res.timings.duration);
  sleep(0.2);

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
  });
  errorRate.add(!success);
  responseTime.add(res.timings.duration);
  sleep(0.2);
}
