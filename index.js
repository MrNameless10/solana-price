const Discord = require('discord.js');
const axios = require('axios');
const client = new Discord.Client();

// Use environment variable for bot token
const TOKEN = process.env.DISCORD_TOKEN;

// Function to get the current Solana price
async function getSolanaPrice() {
    try {
        const response = await axios.get('https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd');
        return response.data.solana.usd;
    } catch (error) {
        console.error(error);
    }
}

// Run the bot
client.once('ready', () => {
    console.log(`Logged in as ${client.user.tag}`);
    setInterval(async () => {
        const sol_price = await getSolanaPrice();
        client.user.setActivity(`SOL: $${sol_price}`, { type: 'PLAYING' });
    }, 60000);  // Updates status every 1 minute
});

client.login(TOKEN);
