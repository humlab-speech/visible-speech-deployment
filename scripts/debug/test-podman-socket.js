#!/usr/bin/env node

/**
 * Test Podman socket compatibility with node-docker-api
 * Mimics the operations performed by session-manager
 */

const Docker = require('node-docker-api').Docker;
const fs = require('fs');

// Get socket path from command line or use default
const socketPath = process.argv[2] || '/var/run/docker.sock';

console.log(`Testing with socket: ${socketPath}`);

// Check if socket exists
if (!fs.existsSync(socketPath)) {
    console.error(`❌ Socket not found at: ${socketPath}`);
    process.exit(1);
}

// Initialize Docker client (works with Podman socket)
const docker = new Docker({ socketPath });

// Test container name
const TEST_CONTAINER_NAME = 'podman-socket-test';
const TEST_IMAGE = 'alpine:latest';

async function runTests() {
    let containerId = null;

    try {
        // Test 1: List containers (like SessionManager.refreshSessions)
        console.log('\n📋 Test 1: List containers');
        const containers = await docker.container.list({ all: true });
        console.log(`   ✓ Listed ${containers.length} containers`);

        // Test 2: Pull test image if needed
        console.log('\n📥 Test 2: Ensure test image exists');
        try {
            await docker.image.get(TEST_IMAGE).status();
            console.log(`   ✓ Image ${TEST_IMAGE} already exists`);
        } catch (error) {
            console.log(`   Pulling ${TEST_IMAGE}...`);
            const stream = await docker.image.create({}, { fromImage: TEST_IMAGE });
            await new Promise((resolve, reject) => {
                stream.on('data', () => {});
                stream.on('end', resolve);
                stream.on('error', reject);
            });
            console.log(`   ✓ Pulled ${TEST_IMAGE}`);
        }

        // Clean up any existing test container
        try {
            const existingContainer = await docker.container.get(TEST_CONTAINER_NAME);
            await existingContainer.delete({ force: true });
            console.log(`   ✓ Cleaned up existing test container`);
        } catch (error) {
            // Container doesn't exist, that's fine
        }

        // Test 3: Create container (like Session.createContainer)
        console.log('\n🐳 Test 3: Create container');
        const container = await docker.container.create({
            Image: TEST_IMAGE,
            name: TEST_CONTAINER_NAME,
            Cmd: ['/bin/sh', '-c', 'echo "Hello from Podman!" && sleep 30'],
            AttachStdout: true,
            AttachStderr: true,
            Tty: false,
        });
        containerId = container.id;
        console.log(`   ✓ Created container: ${containerId.substring(0, 12)}`);

        // Test 4: Start container
        console.log('\n▶️  Test 4: Start container');
        await container.start();
        console.log(`   ✓ Started container`);

        // Test 5: Inspect container (like SessionManager inspects sessions)
        console.log('\n🔍 Test 5: Inspect container');
        const status = await container.status();
        console.log(`   ✓ Container state: ${status.data.State.Status}`);
        console.log(`   ✓ Container name: ${status.data.Name}`);

        // Test 6: Execute command in container (like Session.runCommand)
        console.log('\n⚡ Test 6: Execute command in container');
        const exec = await container.exec.create({
            Cmd: ['/bin/sh', '-c', 'echo "Command execution test" && date'],
            AttachStdout: true,
            AttachStderr: true,
        });

        const execStream = await exec.start();
        let output = '';
        execStream.on('data', chunk => {
            output += chunk.toString();
        });

        await new Promise((resolve, reject) => {
            execStream.on('end', resolve);
            execStream.on('error', reject);
        });

        console.log(`   ✓ Command executed successfully`);
        console.log(`   Output: ${output.trim().split('\n')[0]}`);

        // Test 7: Get container logs
        console.log('\n📜 Test 7: Get container logs');
        const logStream = await container.logs({
            stdout: true,
            stderr: true,
            follow: false,
        });

        let logs = '';
        logStream.on('data', chunk => {
            logs += chunk.toString();
        });

        await new Promise((resolve) => {
            logStream.on('end', resolve);
        });

        console.log(`   ✓ Retrieved logs (${logs.length} bytes)`);
        if (logs.includes('Hello from Podman')) {
            console.log(`   ✓ Log output verified`);
        }

        // Test 8: Stop container
        console.log('\n⏸️  Test 8: Stop container');
        await container.stop();
        console.log(`   ✓ Stopped container`);

        // Test 9: Delete container (like Session.delete)
        console.log('\n🗑️  Test 9: Delete container');
        await container.delete({ force: true });
        console.log(`   ✓ Deleted container`);

        console.log('\n✅ All tests passed!');
        console.log('\nConclusion: node-docker-api is fully compatible with Podman socket');
        console.log('Session-manager container operations should work without code changes.');

    } catch (error) {
        console.error('\n❌ Test failed:', error.message);

        // Clean up on error
        if (containerId) {
            try {
                const container = await docker.container.get(containerId);
                await container.delete({ force: true });
                console.log('Cleaned up test container');
            } catch (cleanupError) {
                // Ignore cleanup errors
            }
        }

        process.exit(1);
    }
}

// Run tests
console.log('\nStarting compatibility tests...');
runTests();
