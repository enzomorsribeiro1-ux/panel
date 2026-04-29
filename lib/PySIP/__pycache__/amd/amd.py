import asyncio
import logging
import threading
from enum import Enum, auto
from dataclasses import dataclass
import queue

from ..utils.logger import logger


@dataclass
class DefaultSettings:
     initial_silence: int = 2500
     greeting: int = 1500
     after_greeting_silence: int = 800
     total_analysis_time: int = 5000
     minimum_word_length: int = 100
     between_words_silence: int = 50
     maximum_number_of_words: int = 2
     silence_threshold: int = 256
     maximum_word_length: int = 5000


class AmdStatus(Enum):
     HUMAN = auto()
     MACHINE = auto()
     NOTSURE = auto()
     HANGUP = auto()


class AnswringMachineDetector:
     def __init__(self) -> None:
          self.settings = DefaultSettings()
          self.amd_status = AmdStatus.NOTSURE
          self.amd_started = threading.Event()

     def run_detector(
          self,
          input_q: queue.Queue,
          _callbacks,
          loop
     ):
          logger.log(
               logging.INFO,
               "AMD is fully disabled — no detection happening"
          )

          self.amd_status = AmdStatus.NOTSURE

          for _cb in _callbacks:
               asyncio.run_coroutine_threadsafe(
                    _cb(self.amd_status),
                    loop
               )
