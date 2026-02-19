const { Client, Intents } = require('discord.js');
const { token, prefix } = require('./config/config');
const commandHandler = require('./commands/index');
const eventHandler = require('./events/index');

const client = new Client({ intents: [Intents.FLAGS.GUILDS, Intents.FLAGS.GUILD_MESSAGES] });

client.once('ready', () => {
    console.log(`Logged in as ${client.user.tag}!`);
});

client.on('messageCreate', (message) => {
    if (!message.content.startsWith(prefix) || message.author.bot) return;

    const args = message.content.slice(prefix.length).trim().split(/ +/);
    const commandName = args.shift().toLowerCase();

    commandHandler.execute(commandName, message, args);
});

eventHandler(client);

client.login(token);