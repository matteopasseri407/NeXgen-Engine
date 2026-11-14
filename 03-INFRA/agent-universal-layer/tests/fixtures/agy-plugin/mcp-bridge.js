import path from "node:path";
import fs from "node:fs/promises";

export const OPENCLAW_MCP_SERVER_PREFIX = "openclaw__";

const defaultIo = {
    mkdir: async (dir) => fs.mkdir(dir, { recursive: true }),
    readFile: async (file) => fs.readFile(file, "utf-8"),
    writeFile: async (file, data) => fs.writeFile(file, data, "utf-8"),
};

async function readJson(io, filePath) {
    try {
        const content = await (io.readFile ? io.readFile(filePath) : fs.readFile(filePath, "utf-8"));
        return JSON.parse(content);
    } catch {
        return {};
    }
}

export function buildInjectedServers(settings) {
    const servers = settings?.mcpServers ?? settings?.mcp?.servers ?? settings ?? {};
    return Object.fromEntries(
        Object.entries(servers).map(([k, v]) => [
            k.startsWith(OPENCLAW_MCP_SERVER_PREFIX) ? k : `${OPENCLAW_MCP_SERVER_PREFIX}${k}`,
            v,
        ])
    );
}

export function mergeAgyMcpServers(existing, injected) {
    const base = existing && typeof existing === "object" ? { ...existing } : {};
    const servers = base.mcpServers && typeof base.mcpServers === "object" ? { ...base.mcpServers } : {};
    return {
        ...base,
        mcpServers: {
            ...servers,
            ...injected,
        },
    };
}

export function stripOwnedServers(current) {
    if (!current || typeof current !== "object" || !current.mcpServers)
        return current ?? {};
    const filtered = Object.fromEntries(
        Object.entries(current.mcpServers).filter(([k]) => !k.startsWith(OPENCLAW_MCP_SERVER_PREFIX))
    );
    return {
        ...current,
        mcpServers: filtered,
    };
}

export async function applyOpenClawMcpBridge(params) {
    const io = params.io ?? defaultIo;
    const injected = buildInjectedServers(await readJson(io, params.settingsPath));
    if (Object.keys(injected).length === 0)
        return undefined;
    await io.mkdir(path.dirname(params.agyConfigPath));
    const existing = await readJson(io, params.agyConfigPath);
    await io.writeFile(params.agyConfigPath, `${JSON.stringify(mergeAgyMcpServers(existing, injected), null, 2)}\n`);
    return {
        injected: Object.keys(injected).length,
        cleanup: async () => {
            const current = await readJson(io, params.agyConfigPath);
            await io.writeFile(params.agyConfigPath, `${JSON.stringify(stripOwnedServers(current), null, 2)}\n`);
        },
    };
}
