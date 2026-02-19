const { Client } = require('discord.js');
const { token } = require('../config/config');

const client = new Client();

client.on('ready', () => {
    console.log(`Logged in as ${client.user.tag}!`);
});

client.on('messageCreate', (message) => {
    if (message.author.bot) return;
    // Handle commands or other message events here
});

client.on('guildMemberAdd', (member) => {
    const channel = member.guild.channels.cache.find(ch => ch.name === 'general');
    if (!channel) return;
    channel.send(`Bienvenue sur le serveur, ${member}!`);
});

module.exports = client;