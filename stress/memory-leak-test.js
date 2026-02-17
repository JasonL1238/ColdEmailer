const axios = require('axios');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const BASE_URL = process.env.BASE_URL || 'http://localhost:8000';
const DURATION_MINUTES = 5;
const REQUESTS_PER_SECOND = 10;
const TOTAL_REQUESTS = DURATION_MINUTES * 60 * REQUESTS_PER_SECOND;

const memoryLog = [];

async function sendRequest(id) {
  try {
    const payload = {
      name: `Memory Test User ${id}`,
      company: `Company ${id}`,
      email: `memory${id}@test.com`,
      status: 'pending',
    };
    
    await axios.post(`${BASE_URL}/api/contacts`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    
    return { success: true, id };
  } catch (error) {
    return { success: false, id, error: error.message };
  }
}

function getMemoryUsage() {
  return new Promise((resolve) => {
    const ps = spawn('ps', ['-o', 'rss=', '-p', process.env.BACKEND_PID || '']);
    let output = '';
    
    ps.stdout.on('data', (data) => {
      output += data.toString();
    });
    
    ps.on('close', () => {
      const rss = parseInt(output.trim());
      resolve(rss ? rss * 1024 : null); // Convert KB to bytes
    });
    
    ps.on('error', () => {
      resolve(null);
    });
  });
}

async function logMemory() {
  const memory = await getMemoryUsage();
  if (memory) {
    const timestamp = new Date().toISOString();
    memoryLog.push({ timestamp, memory: memory / 1024 / 1024 }); // Convert to MB
    console.log(`[${timestamp}] Memory: ${(memory / 1024 / 1024).toFixed(2)} MB`);
  }
}

async function runMemoryLeakTest() {
  console.log('🚀 Starting memory leak detection test');
  console.log(`Target: ${BASE_URL}`);
  console.log(`Duration: ${DURATION_MINUTES} minutes`);
  console.log(`Requests/sec: ${REQUESTS_PER_SECOND}`);
  console.log(`Total requests: ${TOTAL_REQUESTS}\n`);

  console.log('⚠️  Note: Set BACKEND_PID environment variable to monitor backend memory');
  console.log('   Example: BACKEND_PID=12345 node memory-leak-test.js\n');

  const startTime = Date.now();
  const endTime = startTime + (DURATION_MINUTES * 60 * 1000);
  let requestCount = 0;

  // Log initial memory
  await logMemory();

  // Send requests continuously
  const interval = setInterval(async () => {
    if (Date.now() >= endTime) {
      clearInterval(interval);
      await analyzeResults();
      return;
    }

    // Send batch of requests
    const batch = [];
    for (let i = 0; i < REQUESTS_PER_SECOND; i++) {
      batch.push(sendRequest(requestCount++));
    }
    
    await Promise.all(batch);
    
    // Log memory every 30 seconds
    if (requestCount % (REQUESTS_PER_SECOND * 30) === 0) {
      await logMemory();
    }
  }, 1000);

  // Also log memory periodically
  const memoryInterval = setInterval(async () => {
    await logMemory();
  }, 30000);
}

async function analyzeResults() {
  console.log('\n📊 Memory Analysis:');
  
  if (memoryLog.length < 2) {
    console.log('   ⚠️  Not enough memory data collected');
    return;
  }

  const initialMemory = memoryLog[0].memory;
  const finalMemory = memoryLog[memoryLog.length - 1].memory;
  const memoryIncrease = finalMemory - initialMemory;
  const percentIncrease = (memoryIncrease / initialMemory) * 100;

  console.log(`   Initial memory: ${initialMemory.toFixed(2)} MB`);
  console.log(`   Final memory: ${finalMemory.toFixed(2)} MB`);
  console.log(`   Increase: ${memoryIncrease.toFixed(2)} MB (${percentIncrease.toFixed(2)}%)`);

  // Check for memory growth trend
  if (memoryLog.length >= 5) {
    const firstHalf = memoryLog.slice(0, Math.floor(memoryLog.length / 2));
    const secondHalf = memoryLog.slice(Math.floor(memoryLog.length / 2));
    
    const firstAvg = firstHalf.reduce((sum, m) => sum + m.memory, 0) / firstHalf.length;
    const secondAvg = secondHalf.reduce((sum, m) => sum + m.memory, 0) / secondHalf.length;
    
    if (secondAvg > firstAvg * 1.2) {
      console.log('\n⚠️  WARNING: Potential memory leak detected!');
      console.log(`   Memory increased by more than 20% over time`);
      console.log(`   First half average: ${firstAvg.toFixed(2)} MB`);
      console.log(`   Second half average: ${secondAvg.toFixed(2)} MB`);
    } else {
      console.log('\n✅ No significant memory leak detected');
    }
  }

  // Save log to file
  const logFile = path.join(__dirname, 'memory-log.json');
  fs.writeFileSync(logFile, JSON.stringify(memoryLog, null, 2));
  console.log(`\n📝 Memory log saved to: ${logFile}`);

  process.exit(0);
}

runMemoryLeakTest().catch(console.error);
