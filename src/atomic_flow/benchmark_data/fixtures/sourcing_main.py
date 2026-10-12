import asyncio
from agents.base import GeminiAgent
from agents.prompter import PromterAgent
from agents.shield import ShieldAgent
from agents.coder import CoderAgent
from agents.tester import TesterAgent

class ManagerAgent(GeminiAgent):
    def __init__(self, name, role):
        super().__init__(name, role, model="gemini-2.0-flash")
        self.agents = []
        self.shield = None

    def add_agent(self, Agent):
        self.agents.append(Agent)
        return f"agent {Agent.name} added to list"

    def set_shield(self, shield_agent):
        self.shield = shield_agent

    async def run_all(self, task, stream_mode: bool = True):
        tasks = [agent.run(task, stream_mode=stream_mode) for agent in self.agents]
        result = await asyncio.gather(*tasks, return_exceptions=True)
        return result

    async def run_pipeline(self, task, stream_mode: bool = True):
        if not self.agents:
            print("No agents to run tasks")
            return None
        current_input = task
        print(f"===> [{self.name}] запуск конвейра (run pipeline....)")
        for agent in self.agents:
            output = await agent.run(current_input, stream_mode=stream_mode)
            if not output:
                print(f"Конвейр прерван изза ошибок агента {agent.name}")
                return None
            if self.shield:
                print(f"[{self.shield.name}] проверяет агента {agent.name}")
                security_result = await self.shield.chek_security(output)
                
                status = security_result.get('status', 'REJECTED')
                risk = security_result.get('risk_level', 'High')
                print(f"вредикт щита: {status} (Риск: {risk})")
                
                if status == "REJECTED":
                    print(f"🛑 КОНВЕЙЕР БЛОКИРОВАН ПО БЕЗОПАСНОСТИ!")
                    print(f"Причина: {security_result.get('reason')}")
                    print(f"Инструкция: {security_result.get('fix_instruction')}")
                    return None 
            current_input = output
            agent.save_logs()
            print(f"статистика агента:{agent.get_stats()}")
        return current_input  

async def main():
    manager = ManagerAgent(
        "Manager", 
        "ты главный управляющий мульти-агентной системы. Твоя задача координировать работу остальных агентов, ставить задачу и проверять их работу"
    )
    
    promter = PromterAgent("Promter")
    coder = CoderAgent("Coder")
    tester = TesterAgent("Tester")
    shield = ShieldAgent("CyberShield")
    
    manager.set_shield(shield)
    manager.add_agent(promter)
    manager.add_agent(coder)
    manager.add_agent(tester)
    
    await manager.run_pipeline("напиши код для калькулятора для вычислений")  

if __name__ == "__main__":
    asyncio.run(main())