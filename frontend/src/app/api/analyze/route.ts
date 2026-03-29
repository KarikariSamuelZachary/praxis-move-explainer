import { spawn } from 'node:child_process';
import path from 'node:path';

import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';

type AnalyzeRequest = {
  pgn?: string;
};

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as AnalyzeRequest;
    const pgn = body.pgn?.trim();

    if (!pgn) {
      return NextResponse.json(
        { error: 'Missing required field: pgn' },
        { status: 400 }
      );
    }

    const repoRoot = path.resolve(process.cwd(), '..');
    const scriptPath = path.join(repoRoot, 'src', 'run_review.py');
    const pythonBin = process.env.PYTHON_BIN || 'python3';

    const result = await new Promise<{ stdout: string; stderr: string; code: number | null }>((resolve) => {
      const child = spawn(pythonBin, [scriptPath], {
        cwd: repoRoot,
        env: process.env,
      });

      let stdout = '';
      let stderr = '';

      child.stdout.on('data', (chunk: Buffer | string) => {
        stdout += chunk.toString();
      });

      child.stderr.on('data', (chunk: Buffer | string) => {
        stderr += chunk.toString();
      });

      child.on('close', (code) => {
        resolve({ stdout, stderr, code });
      });

      child.stdin.write(pgn);
      child.stdin.end();
    });

    if (result.code !== 0 || result.stderr.trim()) {
      console.error('Python review runner failed:', {
        code: result.code,
        stderr: result.stderr,
      });
      return NextResponse.json(
        { error: 'Failed to analyze PGN' },
        { status: 500 }
      );
    }

    const parsed = JSON.parse(result.stdout);
    return NextResponse.json(parsed, { status: 200 });
  } catch (error) {
    console.error('Analyze API route error:', error);
    return NextResponse.json(
      { error: 'Failed to analyze PGN' },
      { status: 500 }
    );
  }
}
