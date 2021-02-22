import logging
import time
import typing
from pathlib import Path

import numpy as np
import onnxruntime

import gruut
import gruut_ipa

from .constants import (
    TextToSpeechModel,
    TextToSpeechModelConfig,
    TextToSpeechType,
    VocoderModel,
    VocoderModelConfig,
    VocoderType,
)

_LOGGER = logging.getLogger("larynx_runtime")

# -----------------------------------------------------------------------------

# TODO: Variablize sample rate


def text_to_speech(
    text: str,
    gruut_lang: gruut.Language,
    tts_model: TextToSpeechModel,
    vocoder_model: VocoderModel,
    sample_rate: int = 22050,
    number_converters: bool = False,
    disable_currency: bool = False,
    word_indexes: bool = False,
) -> np.ndarray:
    """Tokenize/phonemize text, convert mel spectrograms, then to audio"""
    tokenizer = gruut_lang.tokenizer
    phonemizer = gruut_lang.phonemizer

    phoneme_to_id = getattr(gruut_lang, "phoneme_to_id", None)
    if phoneme_to_id is None:
        phonemes_list = gruut_lang.id_to_phonemes()
        phoneme_to_id = {p: i for i, p in enumerate(phonemes_list)}
        _LOGGER.debug(phoneme_to_id)

        setattr(gruut_lang, "phoneme_to_id", phoneme_to_id)

    sentences = list(
        tokenizer.tokenize(
            text,
            number_converters=number_converters,
            replace_currency=(not disable_currency),
        )
    )

    clean_words: typing.List[str] = []
    text_phonemes: typing.List[str] = []

    for sentence in sentences:
        clean_words.extend(sentence.clean_words)

        # Phonemize each sentence
        sentence_prons = phonemizer.phonemize(
            sentence.tokens, word_indexes=word_indexes, word_breaks=True
        )

        # Pick first pronunciation for each word
        first_pron = []
        for word_prons in sentence_prons:
            if word_prons:
                for phoneme in word_prons[0]:
                    if not phoneme:
                        continue

                    # Split out stress ("ˈa" -> "ˈ", "a")
                    if gruut_ipa.IPA.is_stress(phoneme[0]):
                        first_pron.append(phoneme[0])
                        phoneme = phoneme[1:]

                    first_pron.append(phoneme)

        if not first_pron:
            continue

        # Ensure sentence ends with major break
        if first_pron[-1] != gruut_ipa.IPA.BREAK_MAJOR.value:
            first_pron.append(gruut_ipa.IPA.BREAK_MAJOR.value)

        # Add another major break for good measure
        first_pron.append(gruut_ipa.IPA.BREAK_MAJOR.value)

        text_phonemes.extend(first_pron)

    _LOGGER.debug("Words for '%s': %s", text, clean_words)
    _LOGGER.debug("Phonemes for '%s': %s", text, text_phonemes)

    # Convert to phoneme ids
    phoneme_ids = np.array([phoneme_to_id[p] for p in text_phonemes])

    # Run text to speech
    tts_start_time = time.perf_counter()

    mels = tts_model.phonemes_to_mels(phoneme_ids)
    tts_end_time = time.perf_counter()

    _LOGGER.debug(
        "Got mels in %s second(s) (shape=%s)", tts_end_time - tts_start_time, mels.shape
    )

    # Run vocoder
    vocoder_start_time = time.perf_counter()
    audio = vocoder_model.mels_to_audio(mels)
    vocoder_end_time = time.perf_counter()

    _LOGGER.debug(
        "Got audio in %s second(s) (shape=%s)",
        vocoder_end_time - vocoder_start_time,
        audio.shape,
    )

    audio_duration_sec = audio.shape[-1] / sample_rate
    infer_sec = vocoder_end_time - tts_start_time
    real_time_factor = audio_duration_sec / infer_sec

    _LOGGER.debug(
        "Real-time factor: %0.2f second(s) (audio=%0.2f, infer=%0.2f)",
        real_time_factor,
        audio_duration_sec,
        infer_sec,
    )

    return audio


# -----------------------------------------------------------------------------


def load_tts_model(
    model_type: TextToSpeechType,
    model_path: typing.Union[str, Path],
    no_optimizations: bool = False,
) -> TextToSpeechModel:
    sess_options = onnxruntime.SessionOptions()
    if no_optimizations:
        sess_options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
        )

    config = TextToSpeechModelConfig(
        model_path=Path(model_path), session_options=sess_options
    )

    if model_type == TextToSpeechType.TACOTRON2:
        from .tacotron2 import Tacotron2TextToSpeech

        return Tacotron2TextToSpeech(config)

    raise ValueError(f"Unknown text to speech model type: {model_type}")


# -----------------------------------------------------------------------------


def load_vocoder_model(
    model_type: VocoderType,
    model_path: typing.Union[str, Path],
    no_optimizations: bool = False,
) -> VocoderModel:
    sess_options = onnxruntime.SessionOptions()
    if no_optimizations:
        sess_options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
        )

    config = VocoderModelConfig(
        model_path=Path(model_path), session_options=sess_options
    )

    if model_type == VocoderType.GRIFFIN_LIM:
        from .griffin_lim import GriffinLimVocoder

        return GriffinLimVocoder(config)

    if model_type == VocoderType.HIFI_GAN:
        from .hifi_gan import HiFiGanVocoder

        return HiFiGanVocoder(config)

    raise ValueError(f"Unknown vocoder model type: {model_type}")