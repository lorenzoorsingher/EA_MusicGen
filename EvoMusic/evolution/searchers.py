import gc
import os
import time
import requests
import json
import numpy as np
import random

from evotorch import Problem, Solution
from evotorch.neuroevolution import GymNE, VecGymNE
from evotorch.algorithms import SearchAlgorithm
from evotorch.algorithms.searchalgorithm import SinglePopulationAlgorithmMixin
import torch

from EvoMusic.evolution.fitness import MusicScorer
from EvoMusic.music_generation.generators import MusicGenerator
from EvoMusic.configuration import LLMConfig, LLMPromptOperator, searchConf, evoConf

# ------------------------- Evolutionary Algorithm ------------------------
class LLMPromptGenerator():
    def __init__(self, config: LLMConfig):
        self.config = config

    def query_llm(self, prompt: str):
        """
        Query the LLM API with the given prompt.
        """
        # print(f"Querying LLM with prompt: '{prompt}'")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        data = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You produce prompts used to generate music following the requests of the user. You should always respond with the requested prompts by encasing each one of the **final** produced prompts in <prompt> and </prompt> tags. Like the followings:\n 1. <prompt> A music prompt. </prompt>\n 2. <prompt> Another music prompt. </prompt>",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
            "max_tokens": 5000,
        }
        try:
            response = requests.post(
                self.config.api_uri, headers=headers, data=json.dumps(data)
            )
            response.raise_for_status()
            llm_response = response.json()["choices"][0]["message"]["content"].strip()
            # print(f"LLM responded with: '{llm_response}'")
            return llm_response
        except Exception as e:
            print(f"LLM API request failed: {e}")
            return "A default music prompt."
        
    def parse_llm_response(self, response: str):
        """
        Parse the response from the LLM API and return the prompts.
        """
        prompts = []
        if response.count("<prompt>") != response.count("</prompt>"):
            return prompts

        for answer in response.split("</prompt>"):
            if "<prompt>" in answer:
                prompts.append(answer[answer.index("<prompt>") + 8 :].strip())

        return prompts

    def generate_prompts(self, num_prompts: int):
        prompts = []
        while len(prompts) < num_prompts:
            answers = self.query_llm(
                f"Generate {num_prompts-len(prompts)} diverse prompts for generating music, they should span multiple generes, moods, ..."
            )

            if answers.count("<prompt>") != answers.count("</prompt>"):
                continue

            for answer in answers.split("</prompt>"):
                if "<prompt>" in answer:
                    prompts.append(answer[answer.index("<prompt>") + 8 :].strip())
                if len(prompts) >= num_prompts:
                    break

        return prompts

class LLMEvolutionOperator():
    def __init__(self, operator_config: LLMPromptOperator, LLM: LLMPromptGenerator):
        """
            Initialize the LLM operator with the given configuration.
            
            Args:
                operator_config (LLMPromptOperator): The configuration of the LLM operator.
                LLM (LLMPromptGenerator): The LLM model to use for generating prompts.
        """
        self.config = operator_config
        self.LLM_model = LLM
    
    def apply(self, inputs: list[str]):
        """
            Apply the LLM operator to evolve the population.
            
            Args:
                inputs (list[str]): The population of prompts to evolve.
                
            Returns:
                new_population (list[str]): The evolved population of prompts.
        """
        assert len(inputs) == self.config.input, f"Input size is not equal to the expected size of {self.config.input}"
        output = []
        
        execute = np.random.rand() < self.config.probability
        if not execute:
            # sample from inputs without applying the operator
            return np.random.choice(inputs, self.config.output, replace=False).tolist()
        
        while len(output) < self.config.output:
            # generate new prompts using LLM
            prompts = [f"\n{i+1}. {inputs[i]}" for i in range(len(inputs))]
            LLM_prompt = self.config.prompt.format(prompts=prompts)
            
            answers = self.LLM_model.query_llm(LLM_prompt)
            generated_prompts = self.LLM_model.parse_llm_response(answers)
            
            output += generated_prompts[: self.config.output - len(output)]
            
        return output

        

class PromptSearcher(SearchAlgorithm, SinglePopulationAlgorithmMixin):
    def __init__(
        self,
        problem: Problem,
        search_config: searchConf,
        LLM_config: LLMConfig,
    ):
        SearchAlgorithm.__init__(self, problem)

        self._problem = problem

        self.config = search_config
        self.LLM_model = LLMPromptGenerator(LLM_config)

        self._population = None

        self.generations = 1
        
        # if using LLM evolve mode, initialize the operators
        if self.config.mode == "LLM evolve":
            self.operators = [
                LLMEvolutionOperator(
                    operator_config=operator, 
                    LLM=self.LLM_model
                ) for operator in self.config.LLM_genetic_operators
            ]

        SinglePopulationAlgorithmMixin.__init__(self)

    @property
    def population(self):
        return self._population

    @property
    def problem(self):
        return self._problem

    def sample_population(self, population: list, fitness: torch.Tensor, n:int, sample: bool = False):
        """
            Sample the input individuals from the set of individuals based on their fitness.
            
            Args:
                population (list): The population of prompts.
                fitness (torch.Tensor): The fitness scores of the prompts.
                n (int): The number of individuals to sample.
                sample (bool): Whether to sample the individuals for selection based on their fitness.
                
            Returns:
                selected_population (list): The selected population of prompts.
        """
        if n == 0: return []
        
        if sample:
            # sample the individuals based on their fitness using softmax
            fitness = fitness.view(-1) / self.config.temperature
            fitness = torch.softmax(fitness, dim=0)
            fitness = fitness.cpu().numpy()
            selected_population = np.random.choice(population, n, p=fitness, replace=False).tolist()
        else:
            # select the best individuals
            indices = np.argsort(fitness)
            selected_population = [population[i] for i in indices[:n]]
        
        return selected_population

    def get_elites(self) -> list[str]:
        """
        Get the elite solutions from the population.

        Args:
            indeces (list[int]): the indices of the elite solutions in the population sorted by fitness

        Returns:
            list[str]: the elite solutions
        """
        if self.config.elites == 0:
            return []

        num_elites = int(self.config.elites * self.config.population_size)

        if self.config.sample:
            elites = self.sample_population(self.population.values, self.population.evals, num_elites, self.config.sample)
        else:
            indices = self.population.argsort()
            indices = indices[:num_elites]
            elites = [self.population[i].values for i in indices]
            
        return elites

    def get_novel_prompts(self) -> list[str]:
        """
        Get novel prompts for exploration.

        Returns:
            list[str]: the novel prompts
        """
        if self.config.novel_prompts == 0:
            return []

        num_novel = int(self.config.population_size * self.config.novel_prompts)
        novel_prompts = self.LLM_model.generate_prompts(num_novel)

        return novel_prompts

    def full_LLM_step(self):
        indices = self.population.argsort()
        best_idx = indices[0]
        pop_values = self.population.values
        pop_evals = [self.population[i].evals.item() for i in range(len(indices))]

        ranking = ""
        for i in indices:
            # limit to 2 decimal places
            ranking += f"{i+1}. {pop_values[i]} - {pop_evals[i]*50+50:.2f} / 100\n"

        best = pop_values[best_idx]
        print(f"Population Best: {best} - {pop_evals[best_idx]*50+50} / 100")

        # elites for exploitation
        new_prompts = self.get_elites()

        # novel prompts for exploration
        new_prompts += self.get_novel_prompts()

        # Update the population using LLM by giving it the scores of each prompt and asking for new ones
        while len(new_prompts) < self.config.population_size:
            LLM_prompt = self.config.full_LLM_prompt.format(
                ranking=ranking, 
                num_generate=self.config.population_size - len(new_prompts)
            )

            answers = self.LLM_model.query_llm(LLM_prompt)
            generated_prompts = self.LLM_model.parse_llm_response(answers)
            new_prompts += generated_prompts[: self.config.population_size - len(new_prompts)]
            
        # print("Current Population:\n\t- ", "\n\t- ".join(self._problem.prompts))
        # print("New Population:\n\t- ", "\n\t- ".join(new_prompts))
        print("Finished generating new prompts.")

        # Update the population
        self._population.set_values(new_prompts)

    def tournament_selection(self, population: list[str], fitness: torch.Tensor, n: int):
        """
            Perform tournament selection on the population of prompts.
            
            Args:
                population (list[str]): The population of prompts.
                fitness (torch.Tensor): The fitness values of the population.
                n (int): The number of individuals to select.
                
            Returns:
                selected_population (list[str]): The selected population of prompts.
        """
        # randomly select individuals for the tournament
        selected_idx = np.random.choice(len(population), self.config.tournament_size, replace=False)
        selected_population = [population[i] for i in selected_idx]
        selected_fitness = fitness[selected_idx]
        
        # sample the input individuals based on their fitness
        selected_population = self.sample_population(selected_population, selected_fitness, n, self.config.sample)
        
        return selected_population

    def LLM_evolve_step(self):
        """
        Evolve the population using the LLM genetic operators.
        Apply the operators until the population is full.
        """
        new_population = self.get_elites()
        new_population += self.get_novel_prompts()
        
        old_population = self.population.values
        old_pop_evals = self.population.evals
        
        while len(new_population) < self.config.population_size:
            # select the individuals for the tournament
            selected_population = self.tournament_selection(old_population, old_pop_evals, self.config.LLM_genetic_operators[0].input)
            
            # apply the operators
            for operator in self.operators:
                selected_population = operator.apply(selected_population)
            
            new_population += selected_population[: self.config.population_size - len(new_population)]
            
        self._population.set_values(new_population)

    def _step(self):
        """Perform a step of the solver"""
        # update the population
        if self._population is None:
            self._population = self._problem.generate_batch(self.config.population_size)
        elif self.config.mode == "full LLM":
            self.full_LLM_step()
        elif self.config.mode == "LLM evolve":
            self.LLM_evolve_step()
        else:
            raise ValueError("Invalid search mode")
        
        # print new population
        print("Current Population:\n\t- ", "\n\t- ".join(self._population.values))
        
        self._problem.evaluate(self.population)

class MusicOptimizationProblem(Problem):
    """
    Evotorch Problem for optimizing music prompts or embeddings.
    """
    evo_config: evoConf
    music_generator: MusicGenerator

    def __init__(
        self,
        evolutions_config: evoConf,
        music_generator: MusicGenerator,
    ):
        self.evo_config = evolutions_config
        self.text_mode = self.evo_config.search.mode in ["full LLM", "LLM evolve"]
        
        super().__init__(
            objective_sense="max",
            device=self.evo_config.device,
            solution_length=
                None if self.text_mode
                else self.evo_config.max_seq_len * music_generator.get_embedding_size(),
            dtype=object if self.text_mode else torch.float16,
        )
        
        self.evaluator = MusicScorer(self.evo_config.fitness)
        self.music_generator = music_generator
        self.LLM_model = LLMPromptGenerator(self.evo_config.LLM)
        
        self.sample_time = 0 # time taken to generate one sample in the population
        self.current_time = 0 # time taken to generate the current population
        
        self.generated = 0
        self.total_generated = 0

    def _evaluate(self, solution: Solution):
        """
        Objective function that maps solution vectors to prompts or embeddings, generates music,
        computes embeddings, and evaluates similarity to the target embedding.
        """
        start_time = time.time()
        self.generated += 1

        generator_input = solution.values
        if not self.text_mode:
            # copy the input to a new tensor as the values are read-only
            generator_input = solution.values.clone().detach()
        audio_path = self.music_generator.generate_music(input=generator_input, name=f"music_intermediate")

        # Compute the embedding of the generated music
        fitness = self.evaluator.compute_fitness([audio_path]).squeeze()

        # Clean up generated audio file
        if os.path.exists(audio_path):
            os.remove(audio_path)
            # print(f"Deleted temporary audio file: {audio_path}")

        generation_time = time.time() - start_time
        if self.sample_time == 0: self.sample_time = generation_time
        else: self.sample_time = self.sample_time * 0.9 + generation_time * 0.1
        self.current_time += generation_time
        time_left = self.sample_time * (self.evo_config.search.population_size - self.generated)
        total_time = self.current_time + time_left
        # make into time format so it's easier to read
        total_time = time.strftime("%H:%M:%S", time.gmtime(total_time))
        current_time = time.strftime("%H:%M:%S", time.gmtime(self.current_time))
        
        bar_length = 30
        filled_length = int(bar_length * self.generated // self.evo_config.search.population_size)
        bar = "█" * filled_length + "-" * (bar_length - filled_length)
        print(
            f"Generated {self.generated}/{self.evo_config.search.population_size} |{bar}| "
            f"{(100 * self.generated / self.evo_config.search.population_size):.1f}% "
            f"~ Fitness {fitness:.2f} "
            f"~ Progress {current_time} / {total_time} "
            f"~ Sample Time {generation_time:.2f}s",
            end="\r"
        )
        if self.generated >= self.evo_config.search.population_size:
            self.generated = 0
            self.total_generated += self.evo_config.search.population_size
            print(f"\nFinished generation for this population. Total Time: {self.current_time:.2f}s")
            self.current_time = 0

        solution.set_evals(fitness)

    def _fill(self, values):
        prompts = []
        population = values.shape[0]

        print(
            f"Generating diverse prompts for the initial population of {population} solutions..."
        )

        processed_prompts = []

        while len(processed_prompts) < population:
            # Generate diverse prompts for the initial population
            prompts = self.LLM_model.generate_prompts(population)
            
            # if not in text mode, then check if the embeddings are valid
            if not self.text_mode:
                processed = self.music_generator.preprocess_text(prompts, self.evo_config.max_seq_len)
                processed = [prompt for prompt in processed if prompt.shape[0] == self.evo_config.max_seq_len]
            else:
                processed = prompts
            
            processed_prompts += processed[: population - len(processed_prompts)]
        
    
        if self.text_mode:
            # values is an object array
            for i,prompt in enumerate(prompts):
                values.set_item(i, prompt)
        else:
            processed_prompts = torch.stack(processed_prompts)
            values.copy_(processed_prompts.view(population, -1))
            
        return values
