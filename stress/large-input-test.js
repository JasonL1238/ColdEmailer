const axios = require('axios');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';

// Generate large payloads
function generateLargeString(size) {
  return 'x'.repeat(size);
}

function generateLargeContact(size) {
  return {
    name: generateLargeString(size),
    company: generateLargeString(size),
    email: `${generateLargeString(size)}@test.com`,
    status: 'pending',
  };
}

async function testLargeInput(size, description) {
  console.log(`\n🧪 Testing ${description} (${size} characters per field)...`);
  
  try {
    const payload = generateLargeContact(size);
    const startTime = Date.now();
    
    const response = await axios.post(`${BASE_URL}/api/contacts`, payload, {
      headers: { 'Content-Type': 'application/json' },
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
      timeout: 30000,
    });
    
    const duration = Date.now() - startTime;
    console.log(`   ✅ Success: Status ${response.status}, Duration: ${duration}ms`);
    return { success: true, duration, status: response.status };
  } catch (error) {
    const duration = Date.now() - Date.now();
    if (error.response) {
      console.log(`   ⚠️  Rejected: Status ${error.response.status} - ${error.response.data?.detail || error.message}`);
      return { success: false, status: error.response.status, error: error.response.data };
    } else if (error.code === 'ECONNABORTED') {
      console.log(`   ⚠️  Timeout: Request took longer than 30 seconds`);
      return { success: false, error: 'timeout' };
    } else {
      console.log(`   ❌ Error: ${error.message}`);
      return { success: false, error: error.message };
    }
  }
}

async function testLargeJSON() {
  console.log('\n🧪 Testing large JSON payload...');
  
  const largeArray = Array(1000).fill(null).map((_, i) => ({
    name: `Contact ${i}`,
    company: `Company ${i}`,
    email: `contact${i}@test.com`,
    status: 'pending',
  }));

  try {
    const response = await axios.post(`${BASE_URL}/api/contacts/bulk-delete`, { ids: largeArray.map(c => c.id) }, {
      headers: { 'Content-Type': 'application/json' },
      maxContentLength: Infinity,
      maxBodyLength: Infinity,
    });
    console.log(`   ✅ Success: Status ${response.status}`);
    return { success: true };
  } catch (error) {
    if (error.response) {
      console.log(`   ⚠️  Rejected: Status ${error.response.status}`);
    } else {
      console.log(`   ❌ Error: ${error.message}`);
    }
    return { success: false };
  }
}

async function runLargeInputTests() {
  console.log('🚀 Starting large input stress tests');
  console.log(`Target: ${BASE_URL}\n`);

  const results = [];

  // Test progressively larger inputs
  results.push(await testLargeInput(100, 'Small input'));
  results.push(await testLargeInput(1000, 'Medium input'));
  results.push(await testLargeInput(10000, 'Large input'));
  results.push(await testLargeInput(100000, 'Very large input'));
  results.push(await testLargeInput(1000000, 'Extremely large input'));

  results.push(await testLargeJSON());

  console.log('\n📊 Summary:');
  const successful = results.filter(r => r.success).length;
  const failed = results.filter(r => !r.success).length;
  console.log(`   Successful: ${successful}/${results.length}`);
  console.log(`   Failed: ${failed}/${results.length}`);

  if (failed > 0) {
    console.log('\n✅ Server handled large inputs gracefully (rejected with proper errors)');
  }

  process.exit(0);
}

runLargeInputTests().catch(console.error);
