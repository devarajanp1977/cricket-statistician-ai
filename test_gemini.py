import os
from dotenv import load_dotenv

# Load env immediately
load_dotenv()

from app.engine import CricketQueryEngine

def test_engine():
    print("Initializing Engine...")
    engine = CricketQueryEngine()
    
    query = "Who won the ICC Cricket World Cup in 2011?"
    print(f"\nQuerying: {query}")
    
    # Check what the LLM returns for SQL
    sql, model, prompt = engine.get_sql(query)
    print(f"\nGenerated SQL using model '{model}':\n{sql}")
    
    if sql.startswith("ERROR"):
        print("API test failed.")
        return
        
    print("\nAPI connection successful! LLM generated valid duckdb response string.")
    
    # We won't execute SQL on empty DB to get narrative, or we can mock an execution.
    # But let's test get_narrative directly to verify that works too.
    print("\nTesting narrative generation...")
    narrative, model = engine.get_narrative(query, '[{"outcome_winner": "India"}]')
    print(f"\nNarrative using model '{model}':\n{narrative}")
    
if __name__ == "__main__":
    test_engine()
