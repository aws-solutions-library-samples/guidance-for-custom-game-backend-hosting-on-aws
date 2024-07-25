const redis = require('redis');

class RedisManager {
  constructor(redisEndpoint) {
    this.redisClient = redis.createClient({
      socket: {
        host: redisEndpoint,
        port: 6379,
        tls: true
      }
    });
    this.redisPubSubClient = redis.createClient({
      socket: {
        host: redisEndpoint,
        port: 6379,
        tls: true
      }
    });

    this.channelSubscriptions = new Map();
    this.websockets = new Map();

    this.redisClient.connect();
    this.redisPubSubClient.connect();

    this.redisClient.on('error', (err) => {
      console.error('Redis error:', err);
    });

    this.redisClient.on('end', () => {
      console.log('Redis connection closed');
    });

    this.redisClient.on('reconnecting', () => {
      console.log('Reconnecting to Redis...');
    });

    this.redisPubSubClient.on('error', (err) => {
      console.error('Redis error:', err);
    });

    this.redisPubSubClient.on('end', () => {
      console.log('Redis connection closed');
    });

    this.redisPubSubClient.on('reconnecting', () => {
      console.log('Reconnecting to Redis...');
    });
  }

  setUsername(userID, username) {
    try {
      console.log(`Setting username for ${userID} to ${username}`);
      this.redisClient.set(userID, username);
    } catch (err) {
      console.log("Error in setUsername: " + err);
    }
  }

  async getUsername(userID) {
    try {
      return await this.redisClient.get(userID);
    } catch (err) {
      console.log("Error in getUsername: " + err);
      return null
    }
  }

  subscribeToChannel(channel, ws, listener) {
    try {
      console.log(`Subscribing ${this.websockets.get(ws)} to ${channel}`);
      if (!this.channelSubscriptions.has(channel)) {
        console.log('Channel subscription not set yet on this server, creating...');
        this.channelSubscriptions.set(channel, new Set());
        this.redisPubSubClient.sSubscribe(channel, listener);
        console.log('Done!');
      }
      if (!this.channelSubscriptions.get(channel).has(ws)) {
        this.channelSubscriptions.get(channel).add(ws);
        ws.send(JSON.stringify({ message: `You have joined ${channel}` }));
      } else {
        ws.send(JSON.stringify({ message: `You have already joined ${channel}` }));
      }
    } catch (err) {
      console.log("Error in subscribe: " + err);
    }
  }

  unsubscribeFromChannel(channel, ws) {
    try {
      console.log(`Unsubscribing ${this.websockets.get(ws)} from ${channel}`);
      this.channelSubscriptions.get(channel).delete(ws);
      if (this.channelSubscriptions.get(channel).size === 0) {
        console.log(`No more people on channel, Unsubscribe server from ${channel}`)
        // NOTE: This is now disabled because of a rare error happening in the Redis client, see https://github.com/redis/node-redis/issues/2417 on a similar issue, you can fix this modifying the redis library
        //this.redisPubSubClient.sUnsubscribe(channel); 
        console.log('Done!');
        this.channelSubscriptions.delete(channel);
      }
      ws.send(JSON.stringify({ message: `You have left ${channel}` }));
    }
    catch (err) {
      console.log("Error in unsubscribe: " + err);
    }
  }

  publishToChannel(channel, message) {
    try {
      this.redisClient.publish(channel, message);
    }
    catch (err) {
      console.log("Error in publish: " + err);
    }
  }

  addWebsocket(ws, userID) {
    this.websockets.set(ws, userID);
  }

  removeWebsocket(ws) {
    try {
      const userID = this.websockets.get(ws);
      this.websockets.delete(ws);

      const channelsToRemove = new Set();
      this.channelSubscriptions.forEach((subscriberMap, channel) => {
        subscriberMap.delete(ws);
        if (subscriberMap.size === 0) {
          channelsToRemove.add(channel);
        }
      });

      channelsToRemove.forEach((channel) => {
        console.log(`No more people on channel, Unsubscribe server from ${channel}`)
        // NOTE: This is now disabled because of a rare error happening in the Redis client, see https://github.com/redis/node-redis/issues/2417 on a similar issue, you can fix this modifying the redis library
        //this.redisPubSubClient.sUnsubscribe(channel);
        console.log(`Unsubscribed server from ${channel}`);
        this.channelSubscriptions.delete(channel);
      });

      console.log(`User ${userID} disconnected`);
    } catch (err) {
      console.log("Error in removeWebsocket: " + err);
    }
  }
}

module.exports = RedisManager;
