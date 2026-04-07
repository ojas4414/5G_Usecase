from dataclasses import dataclass
import numpy as np

@dataclass
class AnalyticsMetrics:
    total_people_detected: int
    people_in_queue: int
    density: float          # People per square meter
    estimated_wait: float   # Wait time in seconds

def calculate_density(people_count: int, area_sqm: float) -> float:
    if area_sqm <= 0:
        return 0.0
    return round(people_count / area_sqm, 2)

class StatisticalQueuePredictor:
    """
    Stateful Predictor using Little's Law (L = λW) and Exponential Moving Averages (EMA)
    to estimate real dynamic wait times instead of artificial mock weights.
    """
    def __init__(self, alpha=0.2):
        # alpha is the smoothing factor for the EMA of Lambda (processing rate)
        self.alpha = alpha
        self.ema_lambda = 0.0

    def predict_wait(self, current_people: int, current_queued: int, current_lambda: float) -> float:
        # Update our running exponential average of the service rate 
        if current_lambda > 0.0:
            if self.ema_lambda == 0.0:
                self.ema_lambda = current_lambda
            else:
                self.ema_lambda = (self.alpha * current_lambda) + ((1 - self.alpha) * self.ema_lambda)
                
        # If no one is being processed yet, use a pessimistic default baseline service time
        # Assume 1 person = 30 seconds to serve as a backstop
        safe_lambda = self.ema_lambda if self.ema_lambda > 0 else (1.0 / 30.0)
        
        # Little's Law derived standard Wait: W = L / λ (Wait = Queue Count / Service Rate)
        predicted_time = current_queued / safe_lambda
        
        return max(0.0, round(predicted_time, 2))
