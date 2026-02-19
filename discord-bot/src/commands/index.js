const commands = {
    ping: (message, args) => {
        message.channel.send('Pong!');
    },
    echo: (message, args) => {
        const response = args.join(' ') || 'Please provide a message to echo.';
        message.channel.send(response);
    },
    // Add more commands as needed
};

module.exports = commands;