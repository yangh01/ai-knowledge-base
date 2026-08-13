import { spawn } from "bun"
import type { OpencodeClient } from "@opencode-ai/sdk"
import type { Plugin } from "@opencode-ai/plugin"

const VALIDATION_TIMEOUT_MS = 10_000

const isArticlesJsonPath = (filePath: string): boolean => {
  const normalized = filePath.replace(/\\/g, "/")
  if (!normalized.endsWith(".json")) {
    return false
  }
  return (
    normalized.startsWith("knowledge/articles/") ||
    normalized.includes("/knowledge/articles/")
  )
}

const VALIDATION_COMMANDS = [
  ["python", "hooks/validate_json.py"],
  ["python", "hooks/check_quality.py"],
] as const

const runCommand = (
  directory: string,
  filePath: string,
  client: OpencodeClient,
  cmd: readonly string[],
  service: string
): Promise<boolean> => {
  const proc = spawn({
    cmd: [...cmd, filePath],
    cwd: directory,
    stdin: "ignore",
    stdout: "pipe",
    stderr: "pipe",
  })

  const log = (
    level: "info" | "warn" | "error",
    message: string,
    extra: Record<string, unknown>
  ): void => {
    client.app.log({ body: { service, level, message, extra } }).catch(() => {})
  }

  return new Promise<boolean>((resolve) => {
    let timedOut = false
    const timeoutId = setTimeout(() => {
      timedOut = true
      proc.kill()
    }, VALIDATION_TIMEOUT_MS)

    void (async () => {
      try {
        const [stdout, stderr] = await Promise.all([
          new Response(proc.stdout).text(),
          new Response(proc.stderr).text(),
        ])
        const exitCode = proc.exitCode ?? -1
        const report = `${stdout}${stderr}`.trim()
        log(
          timedOut || exitCode !== 0 ? "warn" : "info",
          report || `${service} exit=${exitCode}`,
          { exitCode, filePath, timedOut }
        )
        resolve(!timedOut && exitCode === 0)
      } catch (error) {
        log("error", error instanceof Error ? error.message : String(error), { filePath })
        resolve(false)
      } finally {
        clearTimeout(timeoutId)
      }
    })()
  })
}

const runValidation = (
  directory: string,
  filePath: string,
  client: OpencodeClient
): void => {
  void (async () => {
    for (const cmd of VALIDATION_COMMANDS) {
      const ok = await runCommand(directory, filePath, client, cmd, cmd[cmd.length - 1].slice(0, -3))
      if (!ok) {
        return
      }
    }
  })()
}

export default (async ({ client, directory }) => {
  return {
    "tool.execute.after": async (input) => {
      if (input.tool !== "write" && input.tool !== "edit") {
        return
      }
      const filePath = input.args?.file_path ?? input.args?.filePath
      if (typeof filePath !== "string" || !isArticlesJsonPath(filePath)) {
        return
      }
      runValidation(directory, filePath, client)
    },
  }
}) satisfies Plugin
