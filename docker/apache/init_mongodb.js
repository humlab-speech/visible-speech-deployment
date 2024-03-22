const { MongoClient } = require('mongodb');

const uri = `mongodb://root:${process.env.MONGO_ROOT_PASSWORD}@mongo:27017`;
const client = new MongoClient(uri, { useNewUrlParser: true, useUnifiedTopology: true });

async function insertUser() {
  try {
    await client.connect();

    // Check if 'visp' database exists, create if not
    const database = client.db('visp');
    const adminDb = client.db('admin');
    const databaseExists = await adminDb.admin().listDatabases({ nameOnly: true })
      .then(({ databases }) => databases.some(db => db.name === 'visp'));

    if (!databaseExists) {
      await adminDb.command({ create: 'visp' });
    }

    // Check if 'users' collection exists, create if not
    const collection = database.collection('users');
    const collectionExists = await database.listCollections({ name: 'users' }).hasNext();

    if (!collectionExists) {
      await database.createCollection('users');
    }

    // Replace this with your user object
    const userObject = {
      firstName: 'Test',
      lastName: 'User',
      fullName: 'Test User',
      email: 'testuser@example.com',
      eppn: 'testuser@example.com',
      username: 'testuser_at_example_dot_com',
      phpSessionId: '',
      authorized: false
    };

    const result = await collection.insertOne(userObject);
    console.log(`User inserted with _id: ${result.insertedId}`);
  } finally {
    await client.close();
  }
}

insertUser().catch(console.error);
