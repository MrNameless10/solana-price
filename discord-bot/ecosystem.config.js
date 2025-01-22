module.exports = {
    apps: [
        {
            name: "solana_price",
            script: "discord_bot.py",
            interpreter: "python3",
            watch: true,
            watch_delay: 1000,
            ignore_watch: ["node_modules", "logs"],
            env: {
                DISCORD_TOKEN: process.env.DISCORD_TOKEN,
            },
        },
    ],
};
