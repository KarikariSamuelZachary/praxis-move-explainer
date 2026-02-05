#!/usr/bin/env python3
"""
Praxis Chess Game Analyzer - CLI Proof of Concept

Usage:
    python analyze_game.py --pgn <pgn_file> --color <white|black|both>
    
Example:
    python analyze_game.py --pgn game.pgn --color white
"""
import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from core.game_analyzer import GameAnalyzer
from engines.stockfish_engine import StockfishEngine
from llms.gemini_explainer import GeminiExplainer
from llms.mock_explainer import MockExplainer
from schemas.models import AnalyzedMistake


def format_mistake(analyzed_mistake: AnalyzedMistake) -> str:
    """
    Format an AnalyzedMistake for terminal output.
    
    Args:
        analyzed_mistake: The analyzed mistake to format
        
    Returns:
        Formatted string for display
    """
    mistake = analyzed_mistake.the_mistake
    explanation = analyzed_mistake.the_explanation
    
    eval_before = mistake.evaluation_before.score_cp / 100
    eval_after = mistake.evaluation_after.score_cp / 100
    eval_drop = mistake.eval_drop_cp / 100
    
    output = f"""
{'='*60}
Move {mistake.position_before_move.move_number}: {mistake.move_played} ({mistake.position_before_move.player_color})
{'='*60}

Evaluation: {eval_before:+.1f} → {eval_after:+.1f} pawns (drop: {eval_drop:.1f})
Best move was: {mistake.evaluation_before.best_move_san}

Why it looked good:
{explanation.why_good}

Why it failed:
{explanation.why_failed}

Concept involved:
{explanation.concept_involved}

Typical pattern:
{explanation.typical_pattern}

FEN: {mistake.position_before_move.fen}
{'='*60}
"""
    return output


def main():
    """Main CLI entry point."""
    # Load environment variables
    load_dotenv()
    
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Analyze chess games and explain mistakes in plain English"
    )
    parser.add_argument(
        "--pgn",
        required=True,
        help="Path to PGN file"
    )
    parser.add_argument(
        "--color",
        choices=["white", "black", "both"],
        default="both",
        help="Which player's mistakes to analyze (default: both)"
    )
    parser.add_argument(
        "--stockfish",
        default="stockfish",
        help="Path to Stockfish binary (default: stockfish in PATH)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=100,
        help="Blunder threshold in centipawns (default: 100)"
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=18,
        help="Stockfish analysis depth (default: 18)"
    )
    parser.add_argument(
        "--max-mistakes",
        type=int,
        default=3,
        help="Maximum number of mistakes to analyze (default: 3)"
    )
    parser.add_argument(
        "--model",
        default="gemini-2.0-flash",
        help="Gemini model to use (default: gemini-2.0-flash)"
    )
    parser.add_argument(
        "--llm",
        choices=["gemini", "mock"],
        default="gemini",
        help="LLM provider to use (default: gemini, use 'mock' for testing without API)"
    )
    
    args = parser.parse_args()
    
    # Load Gemini API key (only needed if not using mock)
    if args.llm == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ Error: GEMINI_API_KEY not found in environment", file=sys.stderr)
            print("Create a .env file with: GEMINI_API_KEY=your-key-here", file=sys.stderr)
            print("Or use --llm mock to test without an API", file=sys.stderr)
            sys.exit(1)
    
    # Read PGN file
    pgn_path = Path(args.pgn)
    if not pgn_path.exists():
        print(f"❌ Error: PGN file not found: {args.pgn}", file=sys.stderr)
        sys.exit(1)
    
    with open(pgn_path, 'r') as f:
        pgn_string = f.read()
    
    print("\n" + "="*60)
    print("🎯 PRAXIS CHESS ANALYZER")
    print("="*60)
    print(f"Analyzing {args.color} moves...")
    print(f"Blunder threshold: {args.threshold} centipawns")
    print(f"Stockfish depth: {args.depth}")
    print(f"Using model: {args.model}")
    print("="*60 + "\n")
    
    # Initialize components
    print("⚙️  Initializing Stockfish engine...")
    engine = StockfishEngine(stockfish_path=args.stockfish, depth=args.depth)
    engine.start()
    
    print("⚙️  Initializing LLM explainer...")
    if args.llm == "mock":
        explainer = MockExplainer()
        print("   Using MOCK explainer (no API calls)")
    else:
        explainer = GeminiExplainer(api_key=api_key, model=args.model)
        print(f"   Using Gemini: {args.model}")
    
    print("⚙️  Initializing game analyzer...")
    analyzer = GameAnalyzer(
        engine=engine,
        explainer=explainer,
        blunder_threshold=args.threshold
    )
    
    try:
        print("🔍 Analyzing game...\n")
        mistakes = analyzer.analyze_pgn(pgn_string, args.color)
        
        if not mistakes:
            print("✅ No significant mistakes found!")
            return
        
        # Limit to max mistakes
        mistakes_to_show = mistakes[:args.max_mistakes]
        
        print(f"📊 Found {len(mistakes)} mistake(s). Showing top {len(mistakes_to_show)}:\n")
        
        for analyzed_mistake in mistakes_to_show:
            print(format_mistake(analyzed_mistake))
        
        if len(mistakes) > args.max_mistakes:
            print(f"\n💡 (Showing {args.max_mistakes} of {len(mistakes)} total mistakes)")
    
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        # Clean up
        print("\n🧹 Cleaning up...")
        engine.close()
        print("✅ Done!")


if __name__ == "__main__":
    main()