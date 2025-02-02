from evotorch.algorithms import CMAES, PGPE, XNES, SNES, CEM
from evotorch.algorithms.ga import Cosyne, GeneticAlgorithm, SteadyStateGA
from evotorch.operators import OnePointCrossOver, GaussianMutation, CosynePermutation, MultiPointCrossOver, PolynomialMutation, SimulatedBinaryCrossOver, TwoPointCrossOver

from EvoMusic.music_generation.generators import MusicGenerator
from EvoMusic.evolution.searchers import PromptSearcher
from EvoMusic.evolution.problem import MusicOptimizationProblem
from EvoMusic.evolution.logger import LivePlotter
from EvoMusic.configuration import evoConf

class MusicEvolver:
    def __init__(self, config: evoConf, music_generator: MusicGenerator):
        self.config = config
        self.music_generator = music_generator

        self.problem = MusicOptimizationProblem(config, music_generator)

        if self.problem.text_mode:
            # Initialize the custom PromptSearcher optimizer
            self.optimizer = PromptSearcher(self.problem, config.search, config.LLM)
        else:
            # Initialize the optimizer for embedding optimization
            if config.search.mode == "CMAES":
                default={
                    "problem": self.problem,
                    "stdev_init": 1,
                    "popsize": config.search.population_size,
                }
                new_params = config.search.evotorch
                params = {**default, **new_params}
                self.optimizer = CMAES(**params)
            elif config.search.mode == "PGPE":
                default={
                    "problem": self.problem,
                    "center_learning_rate": 1,
                    "stdev_learning_rate": 1,
                    "stdev_init": 1,
                    "popsize": config.search.population_size,
                }
                new_params = config.search.evotorch
                params = {**default, **new_params}
                self.optimizer = PGPE(**params)
            elif config.search.mode == "XNES":
                default={
                    "problem": self.problem,
                    "stdev_init": 1,
                    "popsize": config.search.population_size,
                }
                new_params = config.search.evotorch
                params = {**default, **new_params}
                self.optimizer = XNES(**params)
            elif config.search.mode == "SNES":
                default = {
                    "problem": self.problem,
                    "popsize": config.search.population_size,
                    "stdev_init": 1,
                }
                new_params = config.search.evotorch
                params = {**default, **new_params}
                self.optimizer = SNES(**params)
            elif config.search.mode == "CEM":
                default = {
                    "problem": self.problem,
                    "popsize": config.search.population_size,
                    "stdev_init": 1,
                    "parenthood_ratio": 0.25,
                }
                new_params = config.search.evotorch
                params = {**default, **new_params}
                self.optimizer = CEM(**params)
            elif config.search.mode == "GA":
                operator_constructors = {
                    "OnePointCrossOver": OnePointCrossOver,
                    "GaussianMutation": GaussianMutation,
                    "CosynePermutation": CosynePermutation,
                    "MultiPointCrossOver": MultiPointCrossOver,
                    "PolynomialMutation": PolynomialMutation,
                    "SimulatedBinaryCrossOver": SimulatedBinaryCrossOver,
                    "TwoPointCrossOver": TwoPointCrossOver,
                }
                default = {
                    "problem": self.problem,
                    "popsize": config.search.population_size,
                    "operators": [
                        operator_constructors[operator.name](self.problem, **operator.parameters)
                        for operator in config.search.GA_operators
                    ],
                    "elitist": True,
                    "re_evaluate": None
                }
                # musicgen std vectors has 18 as mean std and 41 as max
                new_params = config.search.evotorch
                self.problem.epoch_pop = 0
                params = {**default, **new_params}
                self.optimizer = GeneticAlgorithm(**params)
            else:
                raise ValueError(
                    "Invalid searcher specified. Choose between 'CMAES', 'PGPE', 'XNES', 'SNES', 'CEM'."
                )

        # Run the evolution strategy
        print("Starting evolution...")
        LivePlotter(
            self.optimizer,
            self.problem,
            music_generator,
            {
                "search_conf": config.search.__dict__,
                "fitness_conf": config.fitness.__dict__,
                "generation_conf": music_generator.config.__dict__,
                "LLM": {
                    "model": config.LLM.model if config.LLM else "None",
                    "temperature": config.LLM.temperature if config.LLM else "None",
                },
                "evotorch": config.evotorch,
            },
            config.logger,
        )
        
    def evolve(self, n_generations: int=None, user_fitness=None):
        """
        Run the evolution strategy for a specified number of generations
        
        Args:
            n_generations (int): The number of generations to run the evolution for.
                If None, the number of generations specified in the configuration is used.
            user_fitness (function): A custom fitness function to use for evaluation. 
                Can be left as None for music mode
                
        Returns:
            dict: A dictionary containing the best solution and the last generation
                { "best_solution": { "fitness": float, "solution": list }, "last_generation": { "solutions": list, "fitness_values": list } }
        """
        if not n_generations:
            n_generations = self.config.generations
        
        if user_fitness:
            self.problem.evaluator.set_user_fitness(user_fitness)
            
        self.optimizer.run(num_generations=n_generations)
        
        # Get the best solution
        best_fitness = self.optimizer.status["pop_best_eval"]
        best_sol = self.optimizer.status["pop_best"].values
        print("--- Evolution Complete ---")
        
        last_gen = self.optimizer.population.values
        last_gen = [last_gen[i] for i in range(len(last_gen))]
        last_gen_evals = self.optimizer.population.evals.view(-1).tolist()
        
        return {
            "best_solution": {
                "fitness": best_fitness,
                "solution": best_sol                  
            },
            "last_generation": {
                "solutions": last_gen,
                "fitness_values": last_gen_evals
            }
        }


if __name__ == "__main__":
    from diffusers.utils.testing_utils import enable_full_determinism
    from EvoMusic.configuration import load_yaml_config
    from EvoMusic.music_generation.generators import EasyRiffPipeline, MusicGenPipeline

    enable_full_determinism()
    
    import torch
    import numpy as np
    import random
    
    # Set random seed for reproducibility
    seed = 42
    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    torch.cuda.seed_all()
    

    # Load environment variables
    config = load_yaml_config("config.yaml")

    # ------------------------- Music Generation Setup ------------------------
    if config.music_model == "riffusion":
        music_generator = EasyRiffPipeline(config.riffusion_pipeline)
    elif config.music_model == "musicgen":
        music_generator = MusicGenPipeline(config.music_generator)
    else:
        raise ValueError(
            "Invalid music model specified. Choose between 'musicgen' and 'riffusion'."
        )

    # Evolve prompts or embeddings
    evolver = MusicEvolver(config.evolution, music_generator)
    results = evolver.evolve(n_generations=config.evolution.generations)

    # Save the best solution
    best_sol = results["best_solution"]["solution"]
    best_fitness = results["best_solution"]["fitness"]
    
    best_audio_path = music_generator.generate_music(
        input=best_sol, 
        name="BestSolution", 
        duration=config.evolution.duration
    )
    print(f"Best solution saved at: {best_audio_path}")
    
    print(f"Best Fitness: {best_fitness}")
