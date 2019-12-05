from .fuzzobjects import FuzzResult

from .myqueues import MyPriorityQueue, QueueManager
from .fuzzqueues import (
    SeedQ,
    SaveQ,
    PrinterQ,
    RoutingQ,
    FilterQ,
    SliceQ,
    JobQ,
    RecursiveQ,
    DryRunQ,
    HttpQueue,
    HttpReceiver,
    AllVarQ,
)

from .fuzzobjects import FuzzResultFactory, FuzzStats, FuzzPayload
from .facade import Facade
from .exception import FuzzExceptBadOptions, FuzzExceptNoPluginError

from .filter import FuzzResFilterSlice

# Python 2 and 3: zip_longest
try:
    from itertools import zip_longest
except ImportError:
    from itertools import izip_longest as zip_longest


# python 2 and 3: iterator
from builtins import object


class sliceit(object):
    def __init__(self, payload, slicestr):
        self.ffilter = FuzzResFilterSlice(filter_string=slicestr)
        self.payload = payload

    def __iter__(self):
        return self

    def count(self):
        return -1

    def __next__(self):
        item = next(self.payload)
        while not self.ffilter.is_visible(item):
            item = next(self.payload)

        return item


class tupleit(object):
    def __init__(self, parent):
        self.parent = parent

    def count(self):
        return self.parent.count()

    def __next__(self):
        return (next(self.parent),)

    def __iter__(self):
        return self


class dictionary(object):
    def __init__(self, payload, encoders_list):
        self.__payload = payload
        self.__encoders = encoders_list
        self.__generator = self._gen() if self.__encoders else None

    def count(self):
        return (self.__payload.count() * len(self.__encoders)) if self.__encoders else self.__payload.count()

    def __iter__(self):
        return self

    def _gen(self):
        while 1:
            try:
                payload_list = next(self.__payload)
            except StopIteration:
                return

            for name in self.__encoders:
                if name.find('@') > 0:
                    string = payload_list
                    for i in reversed(name.split("@")):
                        string = Facade().encoders.get_plugin(i)().encode(string)
                    yield string
                else:
                    plugin_list = Facade().encoders.get_plugins(name)
                    if not plugin_list:
                        raise FuzzExceptNoPluginError(name + " encoder does not exists (-e encodings for a list of available encoders)")

                    for e in plugin_list:
                        yield e().encode(payload_list)

    def __next__(self):
        return next(self.__generator) if self.__encoders else next(self.__payload)


class requestGenerator(object):
    def __init__(self, options):
        self.options = options
        self.seed = options['compiled_seed']
        self._payload_list = []
        self.dictio = self.get_dictio()

        self.stats = FuzzStats.from_requestGenerator(self)

    def stop(self):
        self.stats.cancelled = True
        self.close()

    def restart(self, seed):
        self.seed = seed.history
        self.dictio = self.get_dictio()

    def _check_dictio_len(self, element):
        if len(element) != len(self.options.get_fuzz_words()):
            raise FuzzExceptBadOptions("FUZZ words and number of payloads do not match!")

    def count(self):
        v = self.dictio.count()
        if self.seed.wf_allvars is not None:
            v *= len(self.seed.wf_allvars_set)

        if self.options["compiled_baseline"] is not None:
            v += 1

        return v

    def __iter__(self):
        return self

    def __next__(self):
        if self.stats.cancelled:
            raise StopIteration

        dictio_payload = next(self.dictio)
        if self.stats.processed() == 0 or (self.options["compiled_baseline"] is not None and self.stats.processed() == 1):
            self._check_dictio_len(dictio_payload)

        start_from = 1
        my_seed = self.seed

        if self.options["seed_payload"] and isinstance(dictio_payload[0], FuzzResult):
            my_seed = dictio_payload[0].from_soft_copy()
            my_seed.history.update_from_options(self.options)

            new_res = FuzzResultFactory.from_seed(my_seed.history, dictio_payload[1:], 2)

            new_res.update_from_options(self.options)
            # my_seed.payload.append(FuzzPayload(dictio_payload[0], [None]))

            return new_res

        else:

            new_res = FuzzResultFactory.from_seed(my_seed, dictio_payload, start_from)
            new_res.update_from_options(self.options)

            return new_res

    def close(self):
        for payload in self._payload_list:
            payload.close()

    def get_dictio(self):
        class wrapper(object):
            def __init__(self, iterator):
                self._it = iter(iterator)

            def __iter__(self):
                return self

            def count(self):
                return -1

            def __next__(self):
                return str(next(self._it))

        selected_dic = []
        self._payload_list = []

        if self.options["dictio"]:
            for d in [wrapper(x) for x in self.options["dictio"]]:
                selected_dic.append(d)
        else:
            for payload in self.options["payloads"]:
                try:
                    name, params, slicestr = [x[0] for x in zip_longest(payload, (None, None, None))]
                except ValueError:
                    raise FuzzExceptBadOptions("You must supply a list of payloads in the form of [(name, {params}), ... ]")

                if not params:
                    raise FuzzExceptBadOptions("You must supply a list of payloads in the form of [(name, {params}), ... ]")

                p = Facade().payloads.get_plugin(name)(params)
                self._payload_list.append(p)
                pp = dictionary(p, params["encoder"]) if "encoder" in params else p
                selected_dic.append(sliceit(pp, slicestr) if slicestr else pp)

        if not selected_dic:
            raise FuzzExceptBadOptions("Empty dictionary! Check payload and filter")

        if len(selected_dic) == 1:
            if self.options["iterator"]:
                raise FuzzExceptBadOptions("Several dictionaries must be used when specifying an iterator")
            return tupleit(selected_dic[0])
        elif self.options["iterator"]:
            return Facade().iterators.get_plugin(self.options["iterator"])(*selected_dic)
        else:
            return Facade().iterators.get_plugin("product")(*selected_dic)


class Fuzzer(object):
    def __init__(self, options):
        self.genReq = options.get("compiled_genreq")

        # Create queues
        # genReq ---> seed_queue -> [slice_queue] -> http_queue/dryrun -> [round_robin -> plugins_queue] * N
        # -> [recursive_queue -> routing_queue] -> [filter_queue] -> [save_queue] -> [printer_queue] ---> results

        self.qmanager = QueueManager(options)
        self.results_queue = MyPriorityQueue()

        if options["allvars"]:
            self.qmanager.add("allvars_queue", AllVarQ(options))
        else:
            self.qmanager.add("seed_queue", SeedQ(options))

        if options.get('compiled_prefilter').is_active():
            self.qmanager.add("slice_queue", SliceQ(options))

        if options.get("dryrun"):
            self.qmanager.add("http_queue", DryRunQ(options))
        else:
            # http_queue breaks process rules due to being asynchronous. Someone has to collects its sends, for proper fuzzqueue's count and sync purposes
            self.qmanager.add("http_queue", HttpQueue(options))
            self.qmanager.add("http_receiver", HttpReceiver(options))

        if options.get("script"):
            self.qmanager.add("plugins_queue", JobQ(options))

        if options.get("script") or options.get("rlevel") > 0:
            self.qmanager.add("recursive_queue", RecursiveQ(options))
            rq = RoutingQ(
                options,
                {
                    FuzzResult.seed: self.qmanager["seed_queue"],
                    FuzzResult.backfeed: self.qmanager["http_queue"]
                }
            )

            self.qmanager.add("routing_queue", rq)

        if options.get('compiled_filter').is_active():
            self.qmanager.add("filter_queue", FilterQ(options))

        if options.get('save'):
            self.qmanager.add("save_queue", SaveQ(options))

        if options.get('compiled_printer'):
            self.qmanager.add("printer_queue", PrinterQ(options))

        self.qmanager.bind(self.results_queue)

        # initial seed request
        self.qmanager.start()

    def __iter__(self):
        return self

    def __next__(self):
        # http://bugs.python.org/issue1360
        res = self.results_queue.get()
        self.results_queue.task_done()

        # done! (None sent has gone through all queues).
        if not res:
            raise StopIteration
        elif res.type == FuzzResult.error:
            raise res.exception

        return res

    def stats(self):
        return dict(list(self.qmanager.get_stats().items()) + list(self.qmanager["http_queue"].job_stats().items()) + list(self.genReq.stats.get_stats().items()))

    def cancel_job(self):
        self.qmanager.cancel()

    def pause_job(self):
        self.qmanager["http_queue"].pause.clear()

    def resume_job(self):
        self.qmanager["http_queue"].pause.set()
