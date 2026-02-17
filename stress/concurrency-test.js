const axios = require('axios');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const CONCURRENT_REQUESTS = 100;
const REQUESTS_PER_BATCH = 10;

async function sendRequest(id) {
  try {
    const payload = {
      name: `Concurrent User ${id}`,
      company: `Company ${id}`,
      email: `concurrent${id}@test.com`,
      status: 'pending',
    };
    
    const response = await axios.post(`${BASE_URL}/api/contacts`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    
    return {
      id,
      success: true,
      status: response.status,
      contactId: response.data.id,
    };
  } catch (error) {
    return {
      id,
      success: false,
      error: error.message,
      status: error.response?.status,
    };
  }
}

async function runConcurrencyTest() {
  console.log(`🚀 Starting concurrency test: ${CONCURRENT_REQUESTS} simultaneous requests`);
  console.log(`Target: ${BASE_URL}\n`);

  const startTime = Date.now();
  const batches = [];
  
  // Send requests in batches to avoid overwhelming the system
  for (let i = 0; i < CONCURRENT_REQUESTS; i += REQUESTS_PER_BATCH) {
    const batch = [];
    for (let j = 0; j < REQUESTS_PER_BATCH && (i + j) < CONCURRENT_REQUESTS; j++) {
      batch.push(sendRequest(i + j));
    }
    batches.push(Promise.all(batch));
  }

  const results = await Promise.all(batches);
  const flatResults = results.flat();
  const endTime = Date.now();
  const duration = endTime - startTime;

  // Analyze results
  const successful = flatResults.filter(r => r.success);
  const failed = flatResults.filter(r => !r.success);
  const uniqueContactIds = new Set(successful.map(r => r.contactId).filter(Boolean));

  console.log('\n📊 Results:');
  console.log(`   Total requests: ${CONCURRENT_REQUESTS}`);
  console.log(`   Successful: ${successful.length}`);
  console.log(`   Failed: ${failed.length}`);
  console.log(`   Unique contacts created: ${uniqueContactIds.size}`);
  console.log(`   Duration: ${duration}ms`);
  console.log(`   Requests/sec: ${(CONCURRENT_REQUESTS / (duration / 1000)).toFixed(2)}`);

  if (failed.length > 0) {
    console.log('\n❌ Failures:');
    failed.slice(0, 10).forEach(f => {
      console.log(`   Request ${f.id}: ${f.error} (Status: ${f.status})`);
    });
    if (failed.length > 10) {
      console.log(`   ... and ${failed.length - 10} more failures`);
    }
  }

  // Check for data corruption (duplicate IDs)
  if (uniqueContactIds.size < successful.length) {
    console.log('\n⚠️  WARNING: Some contacts may have been overwritten!');
    console.log(`   Expected ${successful.length} unique IDs, got ${uniqueContactIds.size}`);
  } else {
    console.log('\n✅ No data corruption detected - all contacts are unique');
  }

  // Check for session isolation
  const allContactIds = successful.map(r => r.contactId).filter(Boolean);
  const duplicates = allContactIds.filter((id, index) => allContactIds.indexOf(id) !== index);
  if (duplicates.length > 0) {
    console.log('\n⚠️  WARNING: Duplicate contact IDs detected!');
    console.log(`   Duplicates: ${duplicates.join(', ')}`);
  }

  process.exit(failed.length > CONCURRENT_REQUESTS * 0.1 ? 1 : 0);
}

runConcurrencyTest().catch(console.error);
